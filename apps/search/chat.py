"""Conversational clinical QA chat service.

Manages multi-turn conversations with clinical search context.
Each thread has its own search context (trials + papers) that
the AI uses to answer follow-up questions.
"""

import json
import logging
import os
import urllib.error
import urllib.request

from django.utils.html import strip_tags

from apps.search.models import ChatMessage, ChatThread
from apps.search.services import rewrite_query, run_search

logger = logging.getLogger(__name__)

MAX_HISTORY_TURNS = 10  # Keep last 10 messages for context


def get_or_create_thread(user, thread_id=None):
    """Get existing thread or create a new one."""
    if thread_id:
        try:
            return ChatThread.objects.get(id=thread_id, user=user)
        except ChatThread.DoesNotExist:
            pass
    return ChatThread.objects.create(user=user)


def _needs_new_search(message, thread):
    """Determine if a message needs fresh clinical data or can use existing context."""
    # If thread has no context yet, always search
    if not thread.trials_context and not thread.papers_context:
        return True
    # Check for explicit search intent
    search_triggers = [
        "search for",
        "look up",
        "find",
        "what about",
        "any trials",
        "any studies",
        "new search",
        "compare with",
        "instead of",
    ]
    msg_lower = message.lower()
    return any(trigger in msg_lower for trigger in search_triggers)


def _format_context(thread):
    """Format the thread's search context for the LLM prompt."""
    parts = []
    if thread.trials_context:
        parts.append("=== CLINICAL TRIALS (from clinicaltrials.gov) ===")
        for t in thread.trials_context[:8]:
            parts.append(
                f"- [{t.get('nct_id', '')}] {t.get('title', '')} "
                f"(Status: {t.get('status', '')})"
            )
    if thread.papers_context:
        parts.append("\n=== RESEARCH PAPERS (from PubMed) ===")
        for p in thread.papers_context[:8]:
            parts.append(f"- [PMID: {p.get('pmid', '')}] {p.get('title', '')}")
            parts.append(
                f"  {p.get('authors', '')} | {p.get('journal', '')} "
                f"| {p.get('pub_date', '')}"
            )
    return "\n".join(parts)


def _format_history(thread):
    """Format recent conversation history for the LLM."""
    messages = thread.messages.order_by("-created_at")[:MAX_HISTORY_TURNS]
    messages = list(reversed(messages))
    return [{"role": m.role, "content": m.content} for m in messages]


def chat(user, message, thread_id=None):
    """Process a chat message and return the AI response.

    1. Get or create thread
    2. If new topic or explicit search request → fetch fresh data
    3. Build prompt with conversation history + search context
    4. Call Claude Haiku
    5. Save both messages to thread
    6. Return response + thread info
    """
    thread = get_or_create_thread(user, thread_id)

    # Save user message
    ChatMessage.objects.create(thread=thread, role="user", content=message)

    # Check if we need to search for new data
    if _needs_new_search(message, thread):
        try:
            search_query = rewrite_query(message)
            trials, papers = run_search(search_query)
            thread.trials_context = trials
            thread.papers_context = papers
            thread.save(update_fields=["trials_context", "papers_context"])
            logger.info(
                "Chat search: %d trials, %d papers for thread=%d",
                len(trials),
                len(papers),
                thread.id,
            )
        except Exception as exc:
            logger.warning("Chat search failed: %s", exc)

    # Set title from first message
    if not thread.title:
        thread.title = message[:100]
        thread.save(update_fields=["title"])

    # Build prompt
    context = _format_context(thread)
    history = _format_history(thread)

    system_text = (
        "You are a clinical research assistant having a conversation "
        "with a medical clinic staff member.\n\n"
        "RULES:\n"
        "- Answer based on the clinical data context provided below\n"
        "- Cite sources: [NCT...] for trials, [PMID: ...] for papers\n"
        "- If asked something outside the provided context, say you "
        "don't have data on that and suggest they search for it\n"
        "- Be conversational but concise — 2-4 sentences for simple "
        "questions, more for complex ones\n"
        "- No markdown headers or formatting — plain text with "
        "bullet points (-) when listing\n"
        "- Remember the conversation history — don't repeat yourself\n"
    )

    if context:
        system_text += f"\nCLINICAL DATA CONTEXT:\n{context}\n"
    else:
        system_text += (
            "\nNo clinical data loaded yet. If the user asks a "
            "clinical question, suggest they search for specific "
            "terms.\n"
        )

    # Build messages array for Claude API
    api_messages = [{"role": "user", "content": system_text}]
    api_messages.append(
        {
            "role": "assistant",
            "content": "I understand. I'll help answer clinical "
            "questions using the provided research data, citing "
            "sources for every claim.",
        }
    )
    # Add conversation history (skip the last user message — it's the current one)
    for msg in history[:-1]:
        api_messages.append(msg)
    # Add current message
    api_messages.append({"role": "user", "content": message})

    # Call Claude
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        response_text = (
            "I can't generate AI responses right now — the API key "
            "isn't configured. Your search data is still available "
            "in the results below."
        )
    else:
        model = os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")
        payload = {
            "model": model,
            "max_tokens": 400,
            "temperature": 0.3,
            "messages": api_messages,
        }
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                response_text = strip_tags(body["content"][0]["text"]).strip()
        except Exception as exc:
            logger.error("Chat LLM failed: %s", exc)
            response_text = (
                "I'm having trouble processing that right now. "
                "Try again in a moment."
            )

    # Save assistant message
    ChatMessage.objects.create(thread=thread, role="assistant", content=response_text)

    return {
        "thread_id": thread.id,
        "title": thread.title,
        "message": response_text,
        "has_context": bool(thread.trials_context or thread.papers_context),
        "trials_count": len(thread.trials_context or []),
        "papers_count": len(thread.papers_context or []),
    }
