import logging
from datetime import datetime

from django.http import HttpRequest
from ninja import Router, Schema
from ninja.security import django_auth

from apps.search.models import SearchHistory
from apps.search.services import run_search, summarize_search_results
from apps.workflows.models import AuditLog

logger = logging.getLogger(__name__)

search_router = Router(auth=django_auth)


class SearchIn(Schema):
    query: str


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
    """Search clinicaltrials.gov + PubMed, summarize with AI, save to history.

    Error responses:
    - 400 Bad Request: query too short
    - 401 Unauthorized: not authenticated
    - 422 Unprocessable Entity: schema validation failure (automatic)
    """
    query = data.query.strip()
    if not query or len(query) < 3:
        return 400, {"message": "Query must be at least 3 characters."}

    logger.info(
        "Clinical search: query='%s' by user=%s", query[:50], request.user.email
    )

    # Fetch from both APIs in parallel
    trials, papers = run_search(query)
    logger.info("Search results: %d trials, %d papers", len(trials), len(papers))

    # Summarize with AI
    summary = summarize_search_results(query, trials, papers)

    # Save to history
    record = SearchHistory.objects.create(
        user=request.user,
        query=query,
        summary=summary,
        trials_data=trials,
        papers_data=papers,
    )

    # Per CLAUDE.md Rule #12: AuditLog tracks every state mutation
    AuditLog.objects.create(
        entity_type="search",
        entity_id=record.id,
        action="searched",
        details={"query": query, "trials": len(trials), "papers": len(papers)},
        performed_by=request.user,
    )

    # Track in session memory
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
    }


@search_router.get("/history", response=list[SearchHistoryOut])
def search_history(request: HttpRequest):
    """List user's past searches, most recent first.

    Error responses:
    - 401 Unauthorized: not authenticated
    """
    logger.info("Search history requested by user=%s", request.user.email)
    records = SearchHistory.objects.filter(user=request.user)[:20]
    return [
        {
            "id": r.id,
            "query": r.query,
            "summary": r.summary,
            "trials_count": len(r.trials_data) if r.trials_data else 0,
            "papers_count": len(r.papers_data) if r.papers_data else 0,
            "created_at": r.created_at,
        }
        for r in records
    ]
