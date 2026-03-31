import asyncio
import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.request

import httpx
from django.core.cache import cache
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)

CLINICAL_TRIALS_URL = "https://clinicaltrials.gov/api/v2/studies"
PUBMED_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_SUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

# Limits
MAX_QUERY_LENGTH = 500
MAX_RESULTS = 10
TRIALS_CACHE_TTL = 6 * 3600  # 6 hours
PAPERS_CACHE_TTL = 7 * 86400  # 7 days
HTTPX_TIMEOUT = 10.0


def _sanitize_query(query: str) -> str:
    """Sanitize search query — strip injection attempts and limit length."""
    query = query.strip()[:MAX_QUERY_LENGTH]
    # Remove characters that could cause issues with external APIs
    query = re.sub(r"[<>{}|\\^~\[\]]", "", query)
    return query


async def _fetch_clinical_trials(query: str) -> list[dict]:
    """Fetch clinical trials from clinicaltrials.gov v2 API."""
    query = _sanitize_query(query)
    if not query:
        return []

    cache_key = f"search:trials:{hashlib.md5(query.encode()).hexdigest()[:12]}"
    cached = cache.get(cache_key)
    if cached is not None:
        logger.info("Clinical trials cache hit: query=%s", query[:50])
        return cached

    logger.info("Fetching clinical trials: query=%s", query[:50])
    trials = []
    try:
        async with httpx.AsyncClient(timeout=HTTPX_TIMEOUT) as client:
            resp = await client.get(
                CLINICAL_TRIALS_URL,
                params={"query.term": query, "pageSize": MAX_RESULTS},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        logger.warning("Clinical trials API timeout for query=%s", query[:50])
        return []
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Clinical trials API HTTP %d: %s", exc.response.status_code, query[:50]
        )
        return []
    except (httpx.HTTPError, Exception) as exc:
        logger.warning("Clinical trials API error: %s", exc)
        return []

    for study in data.get("studies", []):
        proto = study.get("protocolSection", {})
        if not proto:
            continue
        ident = proto.get("identificationModule", {})
        status_mod = proto.get("statusModule", {})
        desc = proto.get("descriptionModule", {})

        nct_id = ident.get("nctId", "")
        if not nct_id:
            continue

        trials.append(
            {
                "nct_id": nct_id,
                "title": strip_tags(ident.get("briefTitle", "") or "")[:200],
                "status": status_mod.get("overallStatus", "Unknown"),
                "summary": strip_tags((desc.get("briefSummary", "") or "")[:300]),
            }
        )

    cache.set(cache_key, trials, TRIALS_CACHE_TTL)
    logger.info(
        "Clinical trials fetched: %d results for query=%s", len(trials), query[:50]
    )
    return trials


async def _fetch_pubmed_papers(query: str) -> list[dict]:
    """Fetch papers from PubMed using NCBI E-utilities (esearch + esummary)."""
    query = _sanitize_query(query)
    if not query:
        return []

    cache_key = f"search:papers:{hashlib.md5(query.encode()).hexdigest()[:12]}"
    cached = cache.get(cache_key)
    if cached is not None:
        logger.info("PubMed cache hit: query=%s", query[:50])
        return cached

    logger.info("Fetching PubMed papers: query=%s", query[:50])
    try:
        async with httpx.AsyncClient(timeout=HTTPX_TIMEOUT) as client:
            # Step 1: esearch — get PMIDs
            search_resp = await client.get(
                PUBMED_SEARCH_URL,
                params={
                    "db": "pubmed",
                    "term": query,
                    "retmax": MAX_RESULTS,
                    "retmode": "json",
                },
            )
            search_resp.raise_for_status()
            search_data = search_resp.json()
            id_list = search_data.get("esearchresult", {}).get("idlist", [])

            if not id_list:
                cache.set(cache_key, [], PAPERS_CACHE_TTL)
                return []

            # Step 2: esummary — get paper details
            summary_resp = await client.get(
                PUBMED_SUMMARY_URL,
                params={
                    "db": "pubmed",
                    "id": ",".join(id_list),
                    "retmode": "json",
                },
            )
            summary_resp.raise_for_status()
            result = summary_resp.json().get("result", {})
    except httpx.TimeoutException:
        logger.warning("PubMed API timeout for query=%s", query[:50])
        return []
    except httpx.HTTPStatusError as exc:
        logger.warning("PubMed API HTTP %d: %s", exc.response.status_code, query[:50])
        return []
    except (httpx.HTTPError, Exception) as exc:
        logger.warning("PubMed API error: %s", exc)
        return []

    papers = []
    for pmid in id_list:
        paper = result.get(pmid, {})
        if not isinstance(paper, dict):
            continue
        title = strip_tags(paper.get("title", "") or "")
        if not title:
            continue

        authors_list = paper.get("authors", [])
        if not isinstance(authors_list, list):
            authors_list = []
        author_names = ", ".join(
            a.get("name", "") for a in authors_list[:3] if isinstance(a, dict)
        )
        if len(authors_list) > 3:
            author_names += " et al."

        papers.append(
            {
                "pmid": str(pmid),
                "title": title[:300],
                "authors": author_names[:200],
                "journal": strip_tags(paper.get("source", "") or "")[:100],
                "pub_date": str(paper.get("pubdate", ""))[:20],
            }
        )

    cache.set(cache_key, papers, PAPERS_CACHE_TTL)
    logger.info(
        "PubMed papers fetched: %d results for query=%s", len(papers), query[:50]
    )
    return papers


async def search_clinical_data(query: str) -> tuple[list[dict], list[dict]]:
    """Search both clinicaltrials.gov and PubMed in parallel."""
    trials, papers = await asyncio.gather(
        _fetch_clinical_trials(query),
        _fetch_pubmed_papers(query),
        return_exceptions=True,
    )
    # If either raised, return empty list for that source
    if isinstance(trials, Exception):
        logger.error("Clinical trials fetch exception: %s", trials)
        trials = []
    if isinstance(papers, Exception):
        logger.error("PubMed fetch exception: %s", papers)
        papers = []
    return trials, papers


def run_search(query: str) -> tuple[list[dict], list[dict]]:
    """Sync wrapper for the async search."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Already in an async context — run in a new thread
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, search_clinical_data(query))
            return future.result(timeout=25)
    return asyncio.run(search_clinical_data(query))


def summarize_search_results(query: str, trials: list[dict], papers: list[dict]) -> str:
    """Summarize search results using Claude Haiku via direct API call.

    RAG pattern: retrieve (done), format context, augmented generation.
    """
    if not trials and not papers:
        return "No clinical trials or papers found for this query."

    # Format context with citations
    trials_text = ""
    for t in trials:
        trials_text += f"- [{t['nct_id']}] {t['title']} " f"(Status: {t['status']})\n"
        if t.get("summary"):
            trials_text += f"  Summary: {t['summary']}\n"

    papers_text = ""
    for p in papers:
        papers_text += f"- [PMID: {p['pmid']}] {p['title']}\n"
        papers_text += (
            f"  Authors: {p['authors']} | "
            f"Journal: {p['journal']} | Date: {p['pub_date']}\n"
        )

    prompt = (
        "<system-reminder>\n"
        "You are a clinical research assistant summarizing search results "
        "for medical clinic staff.\n"
        "- Answer ONLY based on the trials and papers provided below\n"
        "- Cite every claim: [NCT...] for trials, [PMID: ...] for papers\n"
        "- NEVER make claims not supported by the provided context\n"
        '- If insufficient data, say: "Based on the available results, '
        "there is insufficient data to draw conclusions on "
        '[specific aspect]"\n'
        "- Structure: Opening (1-2 sentences), Trial Landscape "
        "(active/completed/recruiting), Key Research Findings, "
        "Brief Conclusion\n"
        "- Keep under 250 words — be concise and direct\n"
        "- Do NOT add disclaimers about consulting physicians\n"
        "</system-reminder>\n\n"
        "CONTEXT:\n"
        "=== CLINICAL TRIALS (from clinicaltrials.gov) ===\n"
        f"{trials_text or 'No trials found.'}\n\n"
        "=== RESEARCH PAPERS (from PubMed) ===\n"
        f"{papers_text or 'No papers found.'}\n\n"
        f"QUERY: {query}\n\n"
        "Provide a structured summary with citations:"
    )

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not configured")
        return _fallback_summary(trials, papers)

    model = os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")
    payload = {
        "model": model,
        "max_tokens": 500,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": prompt}],
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
            raw = body["content"][0]["text"]
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        logger.error("Claude API HTTP %d: %s", e.code, error_body[:200])
        return _fallback_summary(trials, papers)
    except Exception as exc:
        logger.error("LLM call failed: %s", exc)
        return _fallback_summary(trials, papers)

    summary = strip_tags(raw).strip()
    if not summary:
        return _fallback_summary(trials, papers)

    # Truncate if too long
    words = summary.split()
    if len(words) > 400:
        summary = " ".join(words[:400]) + "... [summary truncated]"

    return summary


def _fallback_summary(trials: list[dict], papers: list[dict]) -> str:
    """Generate a basic summary without AI when LLM is unavailable."""
    parts = []
    if trials:
        statuses = {}
        for t in trials:
            s = t.get("status", "Unknown")
            statuses[s] = statuses.get(s, 0) + 1
        status_str = ", ".join(f"{v} {k}" for k, v in statuses.items())
        parts.append(f"Found {len(trials)} clinical trials ({status_str}).")
    if papers:
        parts.append(f"Found {len(papers)} research papers.")
    if not parts:
        return "No results found."
    return " ".join(parts) + " AI summary unavailable — see results below."
