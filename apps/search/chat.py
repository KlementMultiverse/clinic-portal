"""Intelligent Clinical QA Chat — unified search + conversation.

Implements the "Peeking Under the Hood" pattern from Claude Code:
  Call 1: Classify intent (search / follow-up / general chat)
  Call 2: Rewrite query if search needed
  Call 3: Generate response with full context

Memory layers:
  - Thread context: trials + papers from searches (temporary)
  - Message history: full conversation (permanent)
  - User identity: name remembered across messages
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

MAX_HISTORY_TURNS = 10


def get_or_create_thread(user, thread_id=None):
    if thread_id:
        try:
            return ChatThread.objects.get(id=thread_id, user=user)
        except ChatThread.DoesNotExist:
            pass
    return ChatThread.objects.create(user=user)


def _call_llm(messages, max_tokens=400, temperature=0.3):
    """Call Claude Haiku API."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    model = os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
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
            return strip_tags(body["content"][0]["text"]).strip()
    except Exception as exc:
        logger.error("LLM call failed: %s", exc)
        return None


def _classify_intent(message, has_context):
    """Call 1: Classify user intent.

    Returns: 'search', 'follow_up', or 'general'
    """
    prompt = (
        "Classify this message into exactly one category. "
        "Reply with ONLY the category name, nothing else.\n\n"
        "Categories:\n"
        "- search: user wants to find clinical trials, research papers, "
        "drug info, or medical data (e.g. 'find trials for metformin', "
        "'what research exists on SGLT2', 'look up aspirin studies')\n"
        "- follow_up: user is asking about previously shown results "
        "(e.g. 'which ones are recruiting?', 'tell me more about that', "
        "'what about side effects?')\n"
        "- general: greeting, thanks, or general clinical question\n"
        "- off_topic: anything NOT related to medicine, healthcare, "
        "clinical research, or this clinic system (e.g. politics, "
        "sports, news, math, coding, economics, who is the president, "
        "tell me a joke, write me an essay)\n\n"
        f"Has existing search context: {has_context}\n"
        f"Message: {message}\n"
        "Category:"
    )
    result = _call_llm(
        [{"role": "user", "content": prompt}],
        max_tokens=10,
        temperature=0.0,
    )
    if not result:
        # Fallback: keyword detection
        lower = message.lower()
        search_words = [
            "search",
            "find",
            "look up",
            "trials",
            "studies",
            "research",
            "papers",
            "what about",
            "instead",
        ]
        if any(w in lower for w in search_words):
            return "search"
        if has_context:
            return "follow_up"
        return "search"  # Default to search for clinical questions

    result = result.lower().strip()
    if "off" in result:
        return "off_topic"
    if "search" in result:
        return "search"
    if "follow" in result:
        return "follow_up"
    return "general"


def _format_context(thread):
    """Format search context for LLM prompt."""
    parts = []
    if thread.trials_context:
        parts.append("=== CLINICAL TRIALS ===")
        for t in thread.trials_context[:30]:
            parts.append(
                f"- [{t.get('nct_id', '')}] {t.get('title', '')} "
                f"(Status: {t.get('status', '')})"
            )
    if thread.papers_context:
        parts.append("\n=== RESEARCH PAPERS ===")
        for p in thread.papers_context[:30]:
            parts.append(f"- [PMID: {p.get('pmid', '')}] {p.get('title', '')}")
    return "\n".join(parts)


def _format_history(thread):
    """Get recent conversation as message list."""
    messages = thread.messages.order_by("-created_at")[:MAX_HISTORY_TURNS]
    return [{"role": m.role, "content": m.content} for m in reversed(messages)]


def chat(user, message, thread_id=None):
    """Process a chat message with intent classification.

    Call 1: Classify intent (search / follow_up / general)
    Call 2: Rewrite query if search intent
    Call 3: Generate response with context
    """
    thread = get_or_create_thread(user, thread_id)

    # Save user message
    ChatMessage.objects.create(thread=thread, role="user", content=message)

    # Set title + always bump updated_at so thread moves to top
    if not thread.title:
        thread.title = message[:100]
    thread.save(update_fields=["title", "updated_at"])

    has_context = bool(thread.trials_context or thread.papers_context)

    # ── Call 1: Classify intent ──
    intent = _classify_intent(message, has_context)
    logger.info(
        "Chat intent: %s for thread=%d msg='%s'",
        intent,
        thread.id,
        message[:50],
    )

    # ── Off-topic guard ──
    if intent == "off_topic":
        response_text = (
            "I'm a clinical research assistant — I can only help with "
            "medical and healthcare-related questions. I can search "
            "clinical trials, research papers, and answer questions "
            "about drugs, conditions, and treatments. "
            "What clinical topic can I help you with?"
        )
        ChatMessage.objects.create(
            thread=thread, role="assistant", content=response_text
        )
        return {
            "thread_id": thread.id,
            "title": thread.title,
            "message": response_text,
            "has_context": bool(thread.trials_context or thread.papers_context),
            "trials_count": len(thread.trials_context or []),
            "papers_count": len(thread.papers_context or []),
            "trials": thread.trials_context or [],
            "papers": thread.papers_context or [],
        }

    # ── Call 2: Search if needed ──
    if intent == "search":
        try:
            search_query = rewrite_query(message)
            trials, papers = run_search(search_query)
            thread.trials_context = trials
            thread.papers_context = papers
            thread.save(update_fields=["trials_context", "papers_context"])
            logger.info(
                "Chat search: %d trials, %d papers",
                len(trials),
                len(papers),
            )
        except Exception as exc:
            logger.warning("Chat search failed: %s", exc)

    # ── Call 3: Generate response ──
    context = _format_context(thread)
    history = _format_history(thread)
    user_name = user.name or user.email.split("@")[0]

    # Get user's last 3 searches with actual data for context
    from apps.search.models import SearchHistory

    recent_searches = SearchHistory.objects.filter(user=user).order_by("-created_at")[
        :3
    ]
    search_context = ""
    if recent_searches:
        search_context = (
            "\nUSER'S PREVIOUS SEARCHES (reference only — "
            "mention ONLY if the user asks about past searches):\n"
        )
        for s in recent_searches:
            search_context += f'\nSearch: "{s.query}"\n'
            if s.summary:
                search_context += f"Summary: {s.summary[:150]}\n"
            for t in (s.trials_data or [])[:3]:
                search_context += (
                    f"  [{t.get('nct_id', '')}] "
                    f"{t.get('title', '')[:60]} "
                    f"({t.get('status', '')})\n"
                )
            for p in (s.papers_data or [])[:3]:
                search_context += (
                    f"  [PMID:{p.get('pmid', '')}] " f"{p.get('title', '')[:60]}\n"
                )

    system_text = (
        f"You are a clinical research assistant helping {user_name}. "
        "You have access to clinical trials and research papers.\n\n"
        "RULES:\n"
        "- ONLY answer questions about medicine, healthcare, clinical "
        "research, drugs, conditions, treatments, and this clinic system\n"
        "- If someone asks about politics, sports, news, math, coding, "
        "economics, general knowledge, or ANY non-medical topic, politely "
        "decline and remind them you only handle clinical questions\n"
        "- If clinical data is available below, cite sources: "
        "[NCT...] for trials, [PMID: ...] for papers\n"
        "- Be conversational but precise — 2-5 sentences for "
        "simple questions, more detail if asked\n"
        "- No markdown formatting — plain text with dashes (-) "
        "for lists\n"
        "- Remember the conversation — don't repeat yourself\n"
        "- If the user greets you, respond warmly using their "
        "name and ask how you can help with clinical research\n"
        "- If the user asks about previous/last searches, refer "
        "to their RECENT SEARCHES listed below\n"
        "- If you don't have data on something, say so and "
        "suggest they ask a more specific clinical question\n"
    )

    if search_context:
        system_text += search_context

    if context:
        system_text += f"\nCURRENT SEARCH CONTEXT:\n{context}\n"
    else:
        system_text += (
            "\nNo search data loaded in this conversation yet. "
            "If the user asks a clinical question, you'll "
            "automatically search for relevant trials and papers.\n"
        )

    api_messages = [
        {"role": "user", "content": system_text},
        {
            "role": "assistant",
            "content": f"Hello {user_name}! I'm your clinical "
            "research assistant. I can search real medical "
            "databases and answer questions with citations. "
            "How can I help?",
        },
    ]
    # Add conversation history (skip last — it's the current msg)
    for msg in history[:-1]:
        api_messages.append(msg)
    api_messages.append({"role": "user", "content": message})

    response_text = _call_llm(api_messages)
    if not response_text:
        if intent == "search" and thread.trials_context:
            response_text = (
                f"I found {len(thread.trials_context)} trials and "
                f"{len(thread.papers_context)} papers. "
                "Check the results below — what would you like "
                "to know about them?"
            )
        else:
            response_text = "I'm having trouble right now. Try again in a moment."

    # Save assistant response
    ChatMessage.objects.create(thread=thread, role="assistant", content=response_text)

    return {
        "thread_id": thread.id,
        "title": thread.title,
        "message": response_text,
        "has_context": bool(thread.trials_context or thread.papers_context),
        "trials_count": len(thread.trials_context or []),
        "papers_count": len(thread.papers_context or []),
        "trials": thread.trials_context or [],
        "papers": thread.papers_context or [],
    }
