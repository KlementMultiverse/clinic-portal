import asyncio
import hashlib
import json
import logging
import os
import urllib.error
import urllib.request

import httpx
from django.core.cache import cache
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)

CLINICAL_TRIALS_URL = "https://clinicaltrials.gov/api/v2/studies"
PUBMED_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_SUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


async def _fetch_clinical_trials(query: str) -> list[dict]:
    """Fetch clinical trials from clinicaltrials.gov v2 API."""
    cache_key = f"search:trials:{hashlib.md5(query.encode()).hexdigest()[:12]}"
    cached = cache.get(cache_key)
    if cached is not None:
        logger.info("Clinical trials cache hit for query=%s", query[:50])
        return cached

    logger.info("Fetching clinical trials: query=%s", query[:50])
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                CLINICAL_TRIALS_URL,
                params={"query.term": query, "pageSize": 10},
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.warning("Clinical trials API error: %s", exc)
        return []

    trials = []
    for study in data.get("studies", []):
        proto = study.get("protocolSection", {})
        ident = proto.get("identificationModule", {})
        status = proto.get("statusModule", {})
        desc = proto.get("descriptionModule", {})
        trials.append(
            {
                "nct_id": ident.get("nctId", ""),
                "title": ident.get("briefTitle", ""),
                "status": status.get("overallStatus", ""),
                "summary": (desc.get("briefSummary", "") or "")[:300],
            }
        )

    cache.set(cache_key, trials, 6 * 3600)  # 6hr TTL
    logger.info("Clinical trials fetched: %d results", len(trials))
    return trials


async def _fetch_pubmed_papers(query: str) -> list[dict]:
    """Fetch papers from PubMed using NCBI E-utilities (esearch + esummary)."""
    cache_key = f"search:papers:{hashlib.md5(query.encode()).hexdigest()[:12]}"
    cached = cache.get(cache_key)
    if cached is not None:
        logger.info("PubMed cache hit for query=%s", query[:50])
        return cached

    logger.info("Fetching PubMed papers: query=%s", query[:50])
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Step 1: esearch -- get PMIDs
            search_resp = await client.get(
                PUBMED_SEARCH_URL,
                params={
                    "db": "pubmed",
                    "term": query,
                    "retmax": 10,
                    "retmode": "json",
                },
            )
            search_resp.raise_for_status()
            id_list = search_resp.json().get("esearchresult", {}).get("idlist", [])

            if not id_list:
                cache.set(cache_key, [], 7 * 86400)
                return []

            # Step 2: esummary -- get paper details
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
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.warning("PubMed API error: %s", exc)
        return []

    papers = []
    for pmid in id_list:
        paper = result.get(pmid, {})
        if not isinstance(paper, dict):
            continue
        authors_list = paper.get("authors", [])
        author_names = ", ".join(a.get("name", "") for a in authors_list[:3])
        if len(authors_list) > 3:
            author_names += " et al."
        papers.append(
            {
                "pmid": pmid,
                "title": paper.get("title", ""),
                "authors": author_names,
                "journal": paper.get("source", ""),
                "pub_date": paper.get("pubdate", ""),
            }
        )

    cache.set(cache_key, papers, 7 * 86400)  # 7 day TTL
    logger.info("PubMed papers fetched: %d results", len(papers))
    return papers


async def search_clinical_data(query: str) -> tuple[list[dict], list[dict]]:
    """Search both clinicaltrials.gov and PubMed in parallel."""
    trials, papers = await asyncio.gather(
        _fetch_clinical_trials(query),
        _fetch_pubmed_papers(query),
    )
    return trials, papers


def run_search(query: str) -> tuple[list[dict], list[dict]]:
    """Sync wrapper for the async search."""
    return asyncio.run(search_clinical_data(query))


def summarize_search_results(query: str, trials: list[dict], papers: list[dict]) -> str:
    """Summarize search results using Claude Haiku via direct API call."""
    if not trials and not papers:
        return "No clinical trials or papers found for this query."

    # Format context
    trials_text = ""
    for t in trials:
        trials_text += f"- [{t['nct_id']}] {t['title']} (Status: {t['status']})\n"
        if t.get("summary"):
            trials_text += f"  Summary: {t['summary']}\n"

    papers_text = ""
    for p in papers:
        papers_text += f"- [PMID: {p['pmid']}] {p['title']}\n"
        papers_text += (
            f"  Authors: {p['authors']} | Journal: {p['journal']}"
            f" | Date: {p['pub_date']}\n"
        )

    prompt = (
        "You are a clinical research assistant summarizing search results.\n"
        "- Answer ONLY based on the trials and papers provided below\n"
        "- Cite every claim: [NCT...] for trials, [PMID: ...] for papers\n"
        "- NEVER make claims not supported by the provided context\n"
        '- If insufficient data, say "Based on the available results, '
        'there is insufficient data to draw conclusions on [specific aspect]"\n'
        "- Structure: Opening (1-2 sentences), Trial Landscape "
        "(active/completed/recruiting), Research Findings (key papers),"
        " Conclusion\n"
        "- Keep under 300 words\n\n"
        "CONTEXT:\n"
        "=== CLINICAL TRIALS (from clinicaltrials.gov) ===\n"
        f"{trials_text or 'No trials found.'}\n\n"
        "=== RESEARCH PAPERS (from PubMed) ===\n"
        f"{papers_text or 'No papers found.'}\n\n"
        f"QUERY: {query}\n\n"
        "Summary:"
    )

    # Per CLAUDE.md Rule #9: credentials from os.environ
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not configured, returning raw context")
        return "AI summary unavailable -- API key not configured."

    model = os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")
    url = "https://api.anthropic.com/v1/messages"
    payload = {
        "model": model,
        "max_tokens": 600,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        url,
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
        logger.error("Claude API error: %s", error_body)
        return "AI summary unavailable -- service error."
    except Exception as exc:
        logger.error("LLM call failed: %s", exc)
        return "AI summary unavailable -- service error."

    return strip_tags(raw).strip()
