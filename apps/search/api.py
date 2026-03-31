import logging
from datetime import datetime

from django.http import HttpRequest
from ninja import Router, Schema
from ninja.security import django_auth

from apps.search.models import SearchHistory
from apps.search.services import rewrite_query, run_search, summarize_search_results
from apps.workflows.models import AuditLog

logger = logging.getLogger(__name__)

search_router = Router(auth=django_auth)

MAX_QUERY_LENGTH = 500
MAX_HISTORY = 20


class SearchIn(Schema):
    query: str
    fresh: bool = False  # If True, bypass cache and fetch fresh results


class TrialOut(Schema):
    nct_id: str
    title: str
    status: str
    summary: str = ""


class PaperOut(Schema):
    pmid: str
    title: str
    authors: str = ""
    journal: str = ""
    pub_date: str = ""


class SearchOut(Schema):
    summary: str
    trials: list[TrialOut]
    papers: list[PaperOut]
    search_id: int
    rewritten_query: str = ""  # The optimized query used for search


class SearchHistoryOut(Schema):
    id: int
    query: str
    summary: str
    trials_count: int
    papers_count: int
    created_at: datetime


class MessageOut(Schema):
    message: str


@search_router.post("/", response={200: SearchOut, 400: MessageOut})
def search(request: HttpRequest, data: SearchIn):
    """Search clinicaltrials.gov + PubMed, summarize with AI, save to history."""
    query = data.query.strip()

    # Validation
    if not query or len(query) < 3:
        return 400, {"message": "Query must be at least 3 characters."}
    if len(query) > MAX_QUERY_LENGTH:
        query = query[:MAX_QUERY_LENGTH]

    # Clear cache if fresh requested
    if data.fresh:
        from django.core.cache import cache as _cache

        q_hash = __import__("hashlib").md5(query.encode()).hexdigest()[:12]
        _cache.delete(f"search:trials:{q_hash}")
        _cache.delete(f"search:papers:{q_hash}")
        logger.info("Fresh search requested — cache cleared for query=%s", query[:50])

    # Query rewriting — expand abbreviations and add synonyms
    try:
        search_query = rewrite_query(query)
    except Exception:
        search_query = query

    logger.info(
        "Clinical search: original='%s' rewritten='%s' by user=%s",
        query[:50],
        search_query[:50],
        request.user.email,
    )

    # Fetch from both APIs in parallel
    try:
        trials, papers = run_search(search_query)
    except Exception as exc:
        logger.error("Search failed: %s", exc, exc_info=True)
        trials, papers = [], []

    logger.info("Search results: %d trials, %d papers", len(trials), len(papers))

    # Summarize with AI (use original query for relevance)
    try:
        summary = summarize_search_results(query, trials, papers)
    except Exception as exc:
        logger.error("Summarization failed: %s", exc, exc_info=True)
        summary = "AI summary unavailable — see results below."

    # Save to history
    record = SearchHistory.objects.create(
        user=request.user,
        query=query,
        summary=summary,
        trials_data=trials,
        papers_data=papers,
    )

    AuditLog.objects.create(
        entity_type="search",
        entity_id=record.id,
        action="searched",
        details={
            "query": query[:100],
            "trials": len(trials),
            "papers": len(papers),
        },
        performed_by=request.user,
    )

    try:
        from apps.users.services import track_action

        track_action(request, "searched", "clinical_search", query[:50], record.id)
    except Exception:
        pass

    return 200, {
        "summary": summary,
        "trials": trials,
        "papers": papers,
        "search_id": record.id,
        "rewritten_query": search_query if search_query != query else "",
    }


@search_router.get("/history", response=list[SearchHistoryOut])
def search_history(request: HttpRequest):
    """List user's past searches, most recent first."""
    logger.info("Search history: user=%s", request.user.email)
    records = SearchHistory.objects.filter(user=request.user)[:MAX_HISTORY]
    return [
        {
            "id": r.id,
            "query": r.query,
            "summary": r.summary[:500] if r.summary else "",
            "trials_count": len(r.trials_data) if r.trials_data else 0,
            "papers_count": len(r.papers_data) if r.papers_data else 0,
            "created_at": r.created_at,
        }
        for r in records
    ]
