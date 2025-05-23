"""
Reports API for SMART RAG System

This FastAPI router provides authenticated endpoints for usage analytics, user engagement,
query patterns, and system-level statistics from the `audit` logs and related tables.

Requirements:
- PostgreSQL database with populated `audit`, `document`, `chunk`, `class`, and `user_tokens` tables.
- Valid OAuth-based authentication token (via `verify_token` dependency).
- NLTK `stopwords` resource installed for keyword filtering.

Routes:
- `/reports/users`: Count of distinct users who have submitted queries.
- `/reports/queries`: Number of queries in the last X days (default 30).
- `/reports/top_documents`: Most frequently cited documents in RAG responses.
- `/reports/query_activity`: Daily query counts over a rolling X-day window.
- `/reports/top_keywords`: Most common keywords in user queries, excluding stopwords.
- `/reports/top_phrases`: Most frequent complete user queries (multi-word).
- `/reports/user_activity`: Most active users ranked by query count and activity span.
- `/reports/daily_active_users`: Unique users submitting queries per day.
- `/reports/system_stats`: Aggregated system-level metrics (total queries, chunks, classes, users, etc.).
"""

from typing import Optional
import re

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from nltk.corpus import stopwords

from utils.database import SessionLocal
from .auth_middleware import verify_token

# Define a router for reports
router = APIRouter()


@router.get("/reports/users")
async def get_user_count(user_email: str = Depends(verify_token)):
    """Get the count of unique users who have made queries"""

    db = SessionLocal()
    try:
        # Query to count unique users
        result = db.execute(text("SELECT COUNT(DISTINCT user_email) FROM audit")).scalar()

        return {"user_count": result}
    finally:
        db.close()


@router.get("/reports/queries")
async def get_query_count(
    days: Optional[int] = Query(30, description="Number of days to include in the report"),
    user_email: str = Depends(verify_token),
):
    """Get the count of queries made in the past X days"""

    db = SessionLocal()
    try:
        # Query to count queries in the specified time period
        result = db.execute(
            text(
                "SELECT COUNT(*) FROM audit WHERE event_time > CURRENT_TIMESTAMP - make_interval(days => :days)"
            ),
            {"days": days},
        ).scalar()

        return {"query_count": result, "days": days}
    finally:
        db.close()


@router.get("/reports/top_documents")
async def get_top_documents(
    limit: Optional[int] = Query(10, description="Number of documents to return"),
    user_email: str = Depends(verify_token),
):
    """Get the most frequently referenced documents in responses"""

    db = SessionLocal()
    try:
        # Query to find the most commonly referenced documents
        results = db.execute(
            text(
                """
            SELECT d.class_id, c.class_name, c.authors, COUNT(*) as reference_count
            FROM (
                SELECT UNNEST(document_ids) as document_id
                FROM audit
            ) a
            JOIN document d ON a.document_id = d.document_id
            JOIN class c ON d.class_id = c.class_id
            GROUP BY d.class_id, c.class_name, c.authors
            ORDER BY reference_count DESC
            LIMIT :limit
            """
            ),
            {"limit": limit},
        ).fetchall()

        # Format the results
        formatted_results = [
            {"class_id": row[0], "class_name": row[1], "authors": row[2], "reference_count": row[3]}
            for row in results
        ]

        return formatted_results
    finally:
        db.close()


@router.get("/reports/query_activity")
async def get_query_activity(
    days: Optional[int] = Query(30, description="Number of days to include in the report"),
    user_email: str = Depends(verify_token),
):
    """Get daily query activity for the past X days"""

    db = SessionLocal()
    try:
        # Query to get daily activity
        results = db.execute(
            text(
                """
            SELECT DATE(event_time) as date, COUNT(*) as query_count
            FROM audit
            WHERE event_time > CURRENT_TIMESTAMP - make_interval(days => :days)
            GROUP BY DATE(event_time)
            ORDER BY date
            """
            ),
            {"days": days},
        ).fetchall()

        # Format the results
        formatted_results = [{"date": row[0].isoformat(), "query_count": row[1]} for row in results]

        return formatted_results
    finally:
        db.close()


@router.get("/reports/top_keywords")
async def get_top_keywords(
    limit: Optional[int] = Query(20, description="Number of keywords to return"),
    min_length: Optional[int] = Query(3, description="Minimum keyword length"),
    user_email: str = Depends(verify_token),
):
    """Get the most frequently used keywords in user queries"""

    db = SessionLocal()
    try:
        # Query to get all queries
        all_queries = db.execute(
            text(
                """
                SELECT query FROM audit
                WHERE query IS NOT NULL AND length(query) > 0
                """
            )
        ).fetchall()

        # Process queries to extract keywords
        # Get NLTK stopwords for English
        stop_words = set(stopwords.words("english"))

        # Additional common words to exclude
        additional_stopwords = {
            "give",
            "tell",
            "show",
            "find",
            "does",
            "about",
            "should",
            "could",
            "would",
            "please",
            "help",
            "need",
            "want",
            "get",
            "know",
            "explain",
            "describe",
            "provide",
            "make",
            "create",
        }

        # Combine all stopwords
        all_stopwords = stop_words.union(additional_stopwords)

        # Count keywords
        keyword_counts = {}

        for row in all_queries:
            if not row[0]:  # Skip empty queries
                continue

            query = row[0].lower()

            # Remove punctuation
            query = re.sub(r"[^\w\s]", "", query)

            # Split into words
            words = query.strip().split()

            # Filter out stopwords and short words
            for word in words:
                if (
                    word not in all_stopwords
                    and len(word) >= min_length
                    and word.isalpha()  # Only keep alphabetic words
                ):
                    keyword_counts[word] = keyword_counts.get(word, 0) + 1

        # Convert to list and sort by count
        keywords = [{"keyword": k, "count": v} for k, v in keyword_counts.items()]
        keywords.sort(key=lambda x: x["count"], reverse=True)

        # Return top N results
        return keywords[:limit]
    finally:
        db.close()


@router.get("/reports/top_phrases")
async def get_top_phrases(
    limit: Optional[int] = Query(10, description="Number of phrases to return"),
    min_words: Optional[int] = Query(2, description="Minimum number of words in a phrase"),
    user_email: str = Depends(verify_token),
):
    """Get the most frequently used complete queries as phrases"""

    db = SessionLocal()
    try:
        # Get all complete queries as phrases - fixed GROUP BY clause
        results = db.execute(
            text(
                """
                WITH cleaned_queries AS (
                    SELECT lower(trim(query)) AS clean_query
                    FROM audit
                    WHERE query IS NOT NULL AND length(query) > 0
                )
                SELECT
                    clean_query AS phrase,
                    COUNT(*) AS count,
                    array_length(string_to_array(clean_query, ' '), 1) AS word_count
                FROM cleaned_queries
                GROUP BY clean_query
                HAVING array_length(string_to_array(clean_query, ' '), 1) >= :min_words
                ORDER BY count DESC, phrase
                LIMIT :limit
                """
            ),
            {"limit": limit, "min_words": min_words},
        ).fetchall()

        # Format the results
        formatted_results = [{"phrase": row[0], "count": row[1]} for row in results]

        return formatted_results
    finally:
        db.close()


@router.get("/reports/user_activity")
async def get_user_activity(
    limit: Optional[int] = Query(10, description="Number of users to return"),
    user_email: str = Depends(verify_token),
):
    """Get the most active users by query count"""

    db = SessionLocal()
    try:
        # Query to get most active users
        results = db.execute(
            text(
                """
            SELECT
                user_email,
                COUNT(*) as query_count,
                MIN(event_time) as first_query,
                MAX(event_time) as last_query,
                COUNT(DISTINCT DATE(event_time)) as active_days
            FROM audit
            WHERE user_email IS NOT NULL
            GROUP BY user_email
            ORDER BY query_count DESC
            LIMIT :limit
            """
            ),
            {"limit": limit},
        ).fetchall()

        # Format the results
        formatted_results = [
            {
                "user_email": row[0],
                "query_count": row[1],
                "first_query": row[2].isoformat() if row[2] else None,
                "last_query": row[3].isoformat() if row[3] else None,
                "active_days": row[4],
            }
            for row in results
        ]

        return formatted_results
    finally:
        db.close()


@router.get("/reports/daily_active_users")
async def get_daily_active_users(
    days: Optional[int] = Query(30, description="Number of days to include in the report"),
    user_email: str = Depends(verify_token),
):
    """Get daily active users for the past X days"""

    db = SessionLocal()
    try:
        # Query to get daily active users
        results = db.execute(
            text(
                """
            SELECT
                DATE(event_time) as date,
                COUNT(DISTINCT user_email) as user_count
            FROM audit
            WHERE
                event_time > CURRENT_TIMESTAMP - make_interval(days => :days)
                AND user_email IS NOT NULL
            GROUP BY DATE(event_time)
            ORDER BY date
            """
            ),
            {"days": days},
        ).fetchall()

        # Format the results
        formatted_results = [{"date": row[0].isoformat(), "user_count": row[1]} for row in results]

        return formatted_results
    finally:
        db.close()


@router.get("/reports/system_stats")
async def get_system_stats(user_email: str = Depends(verify_token)):
    """Get overall system statistics"""

    db = SessionLocal()
    try:
        # Query various system metrics
        stats = {}

        # Total users
        stats["total_users"] = db.execute(
            text("SELECT COUNT(DISTINCT user_email) FROM audit")
        ).scalar()

        # Total queries
        stats["total_queries"] = db.execute(text("SELECT COUNT(*) FROM audit")).scalar()

        # Total documents
        stats["total_documents"] = db.execute(text("SELECT COUNT(*) FROM document")).scalar()

        # Total classes
        stats["total_classes"] = db.execute(text("SELECT COUNT(*) FROM class")).scalar()

        # Total chunks
        stats["total_chunks"] = db.execute(text("SELECT COUNT(*) FROM chunk")).scalar()

        # Queries in last 24 hours
        stats["queries_last_24h"] = db.execute(
            text(
                "SELECT COUNT(*) FROM audit WHERE event_time > CURRENT_TIMESTAMP - interval '1 day'"
            )
        ).scalar()

        # Active users in last 24 hours
        stats["active_users_last_24h"] = db.execute(
            text(
                "SELECT COUNT(DISTINCT user_email) FROM audit WHERE event_time > CURRENT_TIMESTAMP - interval '1 day'"
            )
        ).scalar()

        # Average queries per day (last 30 days)
        stats["avg_queries_per_day"] = db.execute(
            text(
                """
            SELECT AVG(query_count) FROM (
                SELECT DATE(event_time) as day, COUNT(*) as query_count
                FROM audit
                WHERE event_time > CURRENT_TIMESTAMP - make_interval(days => 30)
                GROUP BY day
            ) daily_counts
            """
            )
        ).scalar()

        return stats
    finally:
        db.close()
