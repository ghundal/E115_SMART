"""
Semantic Chunker Module for Document Splitting

This module defines an advanced semantic-based document chunker using cosine similarity
between sentence embeddings. It provides the `AdvancedSemanticChunker` class, which is
a LangChain-compatible document transformer that:

- Splits raw text into semantically coherent chunks using configurable similarity thresholds
- Supports various distance-based breakpoint methods (percentile, stddev, IQR, gradient)
- Utilizes SentenceTransformers for local embedding generation (cached)
- Combines context from neighboring sentences via a configurable buffer size
- Optionally controls the number of output chunks or uses automatic thresholding
- Is compatible with LangChain's `Document` structure and transformation pipeline

Intended for preprocessing large textual documents to improve downstream performance
in retrieval-augmented generation (RAG) pipelines or chunk-based semantic search systems.
"""

import copy
import logging
import re
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Literal, Optional, Sequence, Tuple, cast

import numpy as np
from langchain_community.utils.math import cosine_similarity
from langchain_core.documents import BaseDocumentTransformer, Document
from sentence_transformers import SentenceTransformer

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("semantic_chunker")


@lru_cache(maxsize=2)
def get_embedding_model(model_name="all-MiniLM-L6-v2"):
    """Load a sentence transformer model with caching."""
    try:
        logger.info(f"Loading embedding model: {model_name}")
        return SentenceTransformer(model_name)
    except Exception as e:
        logger.exception(f"Error loading embedding model {model_name}: {str(e)}")
        return None


def generate_local_embeddings(
    texts, model_name="all-MiniLM-L6-v2", batch_size=32, preloaded_model=None
):
    """Generate embeddings using a local model (with model reuse)."""
    try:
        # Use preloaded model if provided, otherwise get from cache or load new
        model = preloaded_model if preloaded_model is not None else get_embedding_model(model_name)
        if model is None:
            return [[0.0] * 384] * len(texts)  # Default embedding size

        # Generate embeddings in batches
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            embeddings = model.encode(batch, show_progress_bar=False)
            all_embeddings.extend(embeddings.tolist())

        return all_embeddings
    except Exception as e:
        logger.error(f"Error generating embeddings: {str(e)}")
        return [[0.0] * 384] * len(texts)  # Default embedding size for the model


def combine_sentences(sentences: List[dict], buffer_size: int = 1) -> List[dict]:
    """Combine sentences based on buffer size."""
    for i in range(len(sentences)):
        combined_sentence = ""
        for j in range(i - buffer_size, i):
            if j >= 0:
                combined_sentence += sentences[j]["sentence"] + " "
        combined_sentence += sentences[i]["sentence"]
        for j in range(i + 1, i + 1 + buffer_size):
            if j < len(sentences):
                combined_sentence += " " + sentences[j]["sentence"]
        sentences[i]["combined_sentence"] = combined_sentence
    return sentences


def calculate_cosine_distances(sentences: List[dict]) -> Tuple[List[float], List[dict]]:
    """Calculate cosine distances between sentences."""
    distances: List[float] = []
    for i in range(len(sentences) - 1):
        embedding_current = sentences[i]["combined_sentence_embedding"]
        embedding_next = sentences[i + 1]["combined_sentence_embedding"]
        similarity = cosine_similarity([embedding_current], [embedding_next])[0][0]
        distance: float = float(1 - similarity)
        distances.append(distance)
        sentences[i]["distance_to_next"] = distance
    return distances, sentences


BreakpointThresholdType = Literal["percentile", "standard_deviation", "interquartile", "gradient"]
BREAKPOINT_DEFAULTS: Dict[BreakpointThresholdType, float] = {
    "percentile": 95,
    "standard_deviation": 3,
    "interquartile": 1.5,
    "gradient": 95,
}


class AdvancedSemanticChunker(BaseDocumentTransformer):
    """Split the text based on semantic similarity."""

    def __init__(
        self,
        buffer_size: int = 1,
        add_start_index: bool = False,
        breakpoint_threshold_type: BreakpointThresholdType = "percentile",
        breakpoint_threshold_amount: Optional[float] = None,
        number_of_chunks: Optional[int] = None,
        sentence_split_regex: str = r"(?<=[.?!])\s+",
        embedding_function=None,
        embedding_model: str = "all-MiniLM-L6-v2",
        preloaded_model=None,
    ):
        self._add_start_index = add_start_index
        self.buffer_size = buffer_size
        self.breakpoint_threshold_type = breakpoint_threshold_type
        self.number_of_chunks = number_of_chunks
        self.sentence_split_regex = sentence_split_regex
        self.embedding_model = embedding_model
        self.preloaded_model = preloaded_model

        # Validate breakpoint_threshold_type
        if breakpoint_threshold_type not in BREAKPOINT_DEFAULTS:
            raise ValueError(f"Invalid breakpoint_threshold_type: {breakpoint_threshold_type}")

        if breakpoint_threshold_amount is None:
            self.breakpoint_threshold_amount = BREAKPOINT_DEFAULTS[breakpoint_threshold_type]
        else:
            self.breakpoint_threshold_amount = breakpoint_threshold_amount

        # Create embedding function with specified model
        if embedding_function is None:
            self.embedding_function = lambda texts: generate_local_embeddings(
                texts,
                model_name=self.embedding_model,
                preloaded_model=self.preloaded_model,
            )
        else:
            self.embedding_function = embedding_function

    def _calculate_breakpoint_threshold(self, distances: List[float]) -> Tuple[float, List[float]]:
        if self.breakpoint_threshold_type == "percentile":
            return (
                float(np.percentile(distances, self.breakpoint_threshold_amount)),
                distances,
            )
        elif self.breakpoint_threshold_type == "standard_deviation":
            return (
                float(np.mean(distances) + self.breakpoint_threshold_amount * np.std(distances)),
                distances,
            )
        elif self.breakpoint_threshold_type == "interquartile":
            # Handle multiple percentiles correctly
            percentile_25 = float(np.percentile(distances, 25))
            percentile_75 = float(np.percentile(distances, 75))
            q1 = percentile_25
            q3 = percentile_75
            iqr = q3 - q1
            return (
                float(np.mean(distances) + self.breakpoint_threshold_amount * iqr),
                distances,
            )
        elif self.breakpoint_threshold_type == "gradient":
            distance_gradient = np.gradient(distances, range(0, len(distances)))
            gradient_list = [float(x) for x in distance_gradient]
            return (
                float(np.percentile(distance_gradient, self.breakpoint_threshold_amount)),
                gradient_list,
            )
        else:
            raise ValueError(
                f"Got unexpected `breakpoint_threshold_type`: " f"{self.breakpoint_threshold_type}"
            )

    def _threshold_from_clusters(self, distances: List[float]) -> float:
        """Calculate the threshold based on the number of chunks."""
        if self.number_of_chunks is None:
            raise ValueError("This should never be called if `number_of_chunks` is None.")
        x1, y1 = len(distances), 0.0
        x2, y2 = 1.0, 100.0
        x = max(min(self.number_of_chunks, x1), x2)
        if x2 == x1:
            y = y2
        else:
            y = y1 + ((y2 - y1) / (x2 - x1)) * (x - x1)
        y = min(max(y, 0), 100)
        return cast(float, np.percentile(distances, y))

    def _calculate_sentence_distances(
        self, single_sentences_list: List[str]
    ) -> Tuple[List[float], List[dict]]:
        """Split text into multiple components."""
        _sentences = [{"sentence": x, "index": i} for i, x in enumerate(single_sentences_list)]
        sentences = combine_sentences(_sentences, self.buffer_size)
        embeddings = self.embedding_function([x["combined_sentence"] for x in sentences])
        for i, sentence in enumerate(sentences):
            sentence["combined_sentence_embedding"] = embeddings[i]
        return calculate_cosine_distances(sentences)

    def split_text(
        self,
        text: str,
    ) -> List[str]:
        # Splitting the essay (by default on '.', '?', and '!')
        single_sentences_list = re.split(self.sentence_split_regex, text)
        single_sentences_list = [
            s for s in single_sentences_list if s.strip()
        ]  # Remove empty strings

        # Edge cases
        if len(single_sentences_list) <= 1:
            return single_sentences_list
        if self.breakpoint_threshold_type == "gradient" and len(single_sentences_list) == 2:
            return single_sentences_list

        distances, sentences = self._calculate_sentence_distances(single_sentences_list)

        if self.number_of_chunks is not None:
            breakpoint_distance_threshold = self._threshold_from_clusters(distances)
            breakpoint_array: List[float] = distances
        else:
            breakpoint_distance_threshold, breakpoint_array = self._calculate_breakpoint_threshold(
                distances
            )

        indices_above_thresh = [
            i for i, x in enumerate(breakpoint_array) if x > breakpoint_distance_threshold
        ]

        chunks = []
        start_index = 0

        # Iterate through the breakpoints to slice the sentences
        for index in indices_above_thresh:
            end_index = index
            group = sentences[start_index : end_index + 1]
            combined_text = " ".join([d["sentence"] for d in group])
            chunks.append(combined_text)
            start_index = index + 1

        # The last group, if any sentences remain
        if start_index < len(sentences):
            combined_text = " ".join([d["sentence"] for d in sentences[start_index:]])
            chunks.append(combined_text)

        return chunks

    def create_documents(
        self, texts: List[str], metadatas: Optional[List[dict]] = None
    ) -> List[Document]:
        """Create documents from a list of texts."""
        _metadatas = metadatas or [{}] * len(texts)
        documents = []
        for i, text in enumerate(texts):
            start_index = 0
            for chunk in self.split_text(text):
                metadata = copy.deepcopy(_metadatas[i])
                if self._add_start_index:
                    metadata["start_index"] = start_index
                new_doc = Document(page_content=chunk, metadata=metadata)
                documents.append(new_doc)
                start_index += len(chunk)
        return documents

    def split_documents(self, documents: Iterable[Document]) -> List[Document]:
        """Split documents."""
        texts, metadatas = [], []
        for doc in documents:
            texts.append(doc.page_content)
            metadatas.append(doc.metadata)
        return self.create_documents(texts, metadatas=metadatas)

    def transform_documents(
        self, documents: Sequence[Document], **kwargs: Any
    ) -> Sequence[Document]:
        """Transform sequence of documents by splitting them."""
        return self.split_documents(list(documents))
