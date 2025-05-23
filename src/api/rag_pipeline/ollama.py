"""
Main RAG Orchestration Module for Ollama with Multilingual Support and Extended Timeouts.

This module defines the primary function `query_ollama_with_hybrid_search_multilingual`, which:
- Detects the language of the user query
- Translates non-English queries to English
- Performs a hybrid search over vector and BM25 indexes
- Reranks results using an Ollama LLM
- Queries an Ollama model to generate a response using retrieved context
- Translates the final answer back to the user's original language (if necessary)
- Logs all critical steps for auditability

Key Features:
- Integrated multilingual support with automatic language detection and translation
- Configurable timeout handling for long-running safety checks, reranking, and generation
- Contextual conversation memory support via `chat_history`
- Fine-grained metadata injection and source attribution in responses

Designed for use in secure, production-grade, conversational RAG systems.
"""

from utils.database import log_audit

from .config import DEFAULT_BM25_K, DEFAULT_VECTOR_K, RERANKER_MODEL, logger
from .embedding import embed_query
from .language import detect_language, translate_text
from .ollama_api import format_prompt, query_llm, rerank_with_llm
from .safety import check_query_safety_with_llama_guard
from .search import hybrid_search, retrieve_document_metadata

# Define default timeouts (in seconds)
DEFAULT_SAFETY_TIMEOUT = 3600
DEFAULT_RERANKER_TIMEOUT = 3600
DEFAULT_QUERY_TIMEOUT = 3600


async def query_ollama_with_hybrid_search_multilingual(
    session,
    question,
    embedding_model,
    user_email,
    model_name,
    vector_k=DEFAULT_VECTOR_K,
    bm25_k=DEFAULT_BM25_K,
    chat_history=None,
    safety_timeout=DEFAULT_SAFETY_TIMEOUT,
    reranker_timeout=DEFAULT_RERANKER_TIMEOUT,
    query_timeout=DEFAULT_QUERY_TIMEOUT,
):
    """
    Query the Ollama model using hybrid search with multilingual support.
    Now includes chat history for conversational memory and configurable timeouts.

    Args:
        session: Database session
        question: User's original question in any language
        embedding_model: Model to use for embedding generation
        user_email: User's email for document access control
        model_name: Name of the Ollama model to query
        vector_k: Number of results to retrieve from vector search
        bm25_k: Number of results to retrieve from BM25 search
        chat_history: List of previous conversation messages
        safety_timeout: Timeout in seconds for safety check calls (default: 10 minutes)
        reranker_timeout: Timeout in seconds for reranking calls (default: 10 minutes)
        query_timeout: Timeout in seconds for LLM query calls (default: 10 minutes)
    """
    try:
        # First safety check on original query (any language) with timeout
        is_safe_original, reason_original = await check_query_safety_with_llama_guard(
            question, timeout=safety_timeout
        )
        if not is_safe_original:
            logger.warning(f"Original query failed safety check: {reason_original}")
            return {
                "original_question": question,
                "safety_issue": True,
                "response": f"I cannot process this request: {reason_original}",
                "context_count": 0,
            }

        # Detect original language
        original_language = detect_language(question)
        logger.info(f"Detected language: {original_language}")

        # Translate question to English if not already English
        if original_language != "en":
            english_question = translate_text(
                question, target_lang="en", source_lang=original_language
            )
            logger.info(f"Translated question to English: {english_question}")

            # Second safety check on translated English question with timeout
            is_safe_translated, reason_translated = await check_query_safety_with_llama_guard(
                english_question, timeout=safety_timeout
            )
            if not is_safe_translated:
                logger.warning(f"Translated query failed safety check: {reason_translated}")
                # Translate the rejection reason back to the original language
                rejection_message = f"I cannot process this request: {reason_translated}"
                localized_rejection = translate_text(
                    rejection_message, target_lang=original_language, source_lang="en"
                )
                return {
                    "original_question": question,
                    "english_question": english_question,
                    "safety_issue": True,
                    "response": localized_rejection,
                    "context_count": 0,
                }
        else:
            english_question = question

        # Embed the English query
        query_embedding = embed_query(english_question, embedding_model)
        logger.info(f"Generated query embedding with {len(query_embedding)} dimensions")

        # Perform hybrid search
        context_chunks, sorted_results = hybrid_search(
            session, english_question, query_embedding, vector_k, bm25_k, user_email
        )

        # Apply LLM reranking to combined results with timeout
        reranked_chunks = await rerank_with_llm(
            [item["chunk"] for item in sorted_results[:15]],
            english_question,
            RERANKER_MODEL,
            timeout=reranker_timeout,
        )

        # Get top 3 unique document IDs from reranked chunks
        top_document_ids = []
        seen = set()
        for chunk in reranked_chunks:
            doc_id = chunk["document_id"]
            if doc_id not in seen:
                seen.add(doc_id)
                top_document_ids.append(doc_id)
            if len(top_document_ids) == 3:
                break

        # Get document metadata
        document_metadata = retrieve_document_metadata(session, top_document_ids)

        # Format context chunks for the prompt
        contexts = [
            f"DOCUMENT {i+1}:\n{chunk['chunk_text']}" for i, chunk in enumerate(context_chunks)
        ]
        context = "\n\n".join(contexts)

        conversation_context = ""
        if chat_history and len(chat_history) > 0:
            # Format the last few interactions (limiting to prevent context window issues)
            recent_history = chat_history[-6:]  # Last 3 user/assistant pairs

            conversation_context = "PREVIOUS CONVERSATION:\n"
            for msg in recent_history:
                role = "User" if msg["role"] == "user" else "Assistant"
                conversation_context += f"{role}: {msg['content']}\n\n"

            conversation_context += "CURRENT QUESTION:\n"

        # System prompt - add multilingual instruction if needed
        system_prompt = """
        You are an AI assistant specialized in machine learning, deep learning, and data science.
        You provide helpful, accurate, and educational responses to questions about these topics.

        When answering a query:
        1. Provide clear explanations with appropriate technical detail for the complexity of the question.
        2. When explaining concepts, include practical examples to illustrate how they work.
        3. When relevant, mention advantages, limitations, and common use cases.
        4. Break down your explanation into understandable components.
        5. Maintain a professional and educational tone throughout your responses.
        6. Prioritize information from the context chunks and enhance/format with your knowledge.
        7. Keep your answers concise and to the point.
        8. If you don't know the answer, say so.
        9. Do not include unnecessary information or repetitive explanations.
        10. Format your response clearly and directly address the question.

        IMPORTANT CONVERSATIONAL GUIDELINES:
        - When the user refers to previous questions or answers, use the provided conversation history to maintain context.
        - Use pronouns like "it", "they", "that approach" appropriately when referencing previously discussed concepts.
        - If the user asks a follow-up question about something previously discussed, reference that information in your response.
        - Remember details the user has shared about their project or needs throughout the conversation.
        """

        # For non-English queries, specify that response should be in English first (we'll translate after)
        if original_language != "en":
            system_prompt += "\n\nPlease respond in English. The response will be translated later."

        # Format the prompt with English question
        prompt = format_prompt(system_prompt, context, english_question, conversation_context)

        # Query the LLM with timeout
        english_response = await query_llm(prompt, model_name, timeout=query_timeout)
        logger.info("Successfully generated English response")

        # Create the sources section with document metadata
        sources_section = "\n\nSOURCES:\n"
        for i, doc_id in enumerate(top_document_ids[:3], 1):
            if doc_id in document_metadata:
                meta = document_metadata[doc_id]
                sources_section += f"{i}. [Document ID: {doc_id}] {meta.get('class_name', 'N/A')} by {meta.get('authors', 'N/A')} ({meta.get('term', 'N/A')})\n"

        # Append sources to the English response
        english_response += sources_section

        # Translate response back to original language if not English
        if original_language != "en":
            final_response = translate_text(
                english_response, target_lang=original_language, source_lang="en"
            )
            logger.info(f"Translated response to {original_language}")
        else:
            final_response = english_response

        # Log the original question, English translation, and English response
        log_audit(
            session=session,
            user_email=user_email,
            query=question,  # Log original question
            query_embedding=query_embedding,
            chunks=context_chunks,
            response=english_response,  # Log English response for consistency
            detected_language=original_language,  # Pass the detected language
        )

        # Don't close the session here - let the calling function handle it
        return {
            "original_question": question,
            "detected_language": original_language,
            "english_question": english_question if original_language != "en" else None,
            "context_count": len(context_chunks),
            "response": final_response,  # Return response in original language
            "top_documents": [
                {
                    "document_id": doc_id,
                    "page_number": next(
                        (
                            chunk["page_number"]
                            for chunk in reranked_chunks
                            if chunk["document_id"] == doc_id
                        ),
                        "N/A",
                    ),
                    "class_name": document_metadata.get(doc_id, {}).get("class_name", "N/A"),
                    "authors": document_metadata.get(doc_id, {}).get("authors", "N/A"),
                    "term": document_metadata.get(doc_id, {}).get("term", "N/A"),
                }
                for doc_id in top_document_ids
            ],
        }

    except Exception as e:
        logger.exception(f"Error in query_ollama_with_hybrid_search_multilingual: {str(e)}")
        # Try to translate error message
        error_response = "Sorry, I encountered an error while processing your question."
        if "original_language" in locals() and original_language != "en":
            try:
                error_response = translate_text(
                    error_response, target_lang=original_language, source_lang="en"
                )
            except Exception:
                pass  # If translation fails, use English error

        return {"question": question, "error": str(e), "response": error_response}
