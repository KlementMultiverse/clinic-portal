import logging
from datetime import datetime

from django.db.models import Count
from django.http import HttpRequest
from ninja import Router, Schema
from ninja.security import django_auth

from apps.search.chat import chat as chat_service
from apps.search.models import ChatThread, SearchHistory
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


# ─────────────────────────────────────────────
# Chat endpoints — conversational clinical QA
# ─────────────────────────────────────────────


class ChatIn(Schema):
    message: str
    thread_id: int = None  # None = new conversation


class ChatMessageOut(Schema):
    role: str
    content: str
    created_at: datetime


class ChatOut(Schema):
    thread_id: int
    title: str
    message: str
    has_context: bool
    trials_count: int
    papers_count: int


class ChatThreadOut(Schema):
    id: int
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime


@search_router.post("/chat", response={200: ChatOut, 400: MessageOut})
def chat_message(request: HttpRequest, data: ChatIn):
    """Send a message in a clinical QA chat thread.

    If thread_id is None, starts a new conversation.
    The AI automatically searches clinicaltrials.gov + PubMed
    when needed and remembers the conversation context.
    """
    msg = data.message.strip()
    if not msg or len(msg) < 2:
        return 400, {"message": "Message too short."}
    if len(msg) > 2000:
        return 400, {"message": "Message too long (max 2000 characters)."}

    logger.info(
        "Chat message: thread=%s user=%s msg='%s'",
        data.thread_id,
        request.user.email,
        msg[:50],
    )

    try:
        result = chat_service(
            user=request.user,
            message=msg,
            thread_id=data.thread_id,
        )
    except Exception as exc:
        logger.error("Chat failed: %s", exc, exc_info=True)
        return 400, {"message": "Chat error — try again."}

    try:
        from apps.users.services import track_action

        track_action(request, "chatted", "clinical_chat", msg[:50], result["thread_id"])
    except Exception:
        pass

    return 200, result


@search_router.get("/chat/threads", response=list[ChatThreadOut])
def list_threads(request: HttpRequest):
    """List user's chat threads, most recent first."""
    threads = (
        ChatThread.objects.filter(user=request.user)
        .annotate(msg_count=Count("messages"))
        .order_by("-updated_at")[:20]
    )
    return [
        {
            "id": t.id,
            "title": t.title or f"Chat {t.id}",
            "message_count": t.msg_count,
            "created_at": t.created_at,
            "updated_at": t.updated_at,
        }
        for t in threads
    ]


@search_router.get(
    "/chat/{thread_id}", response={200: list[ChatMessageOut], 404: MessageOut}
)
def get_thread_messages(request: HttpRequest, thread_id: int):
    """Get all messages in a chat thread."""
    try:
        thread = ChatThread.objects.get(id=thread_id, user=request.user)
    except ChatThread.DoesNotExist:
        return 404, {"message": "Thread not found."}
    return 200, [
        {
            "role": m.role,
            "content": m.content,
            "created_at": m.created_at,
        }
        for m in thread.messages.all()
    ]
