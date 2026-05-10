from typing import List, Optional
"""
Enrichment engine — batched architecture.

Flow per firm (regardless of how many data points):
  1. ONE Tavily search  →  domain-scoped if website known, else open web
  2. ONE Gemini call    →  extract ALL data points from that one result set
  3. If any data point came back "not_found", ONE more open-web Tavily search
  4. ONE more Gemini call  →  only for the still-missing data points

Result: 1–2 Tavily calls + 1–2 Gemini calls per firm total,
        instead of N_data_points × 2 calls.  ~5–6× cost reduction.
"""

import asyncio
import json
import os
import re
from typing import Any

import httpx

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
TAVILY_API_KEY     = os.getenv("TAVILY_API_KEY", "")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TAVILY_URL     = "https://api.tavily.com/search"

MODEL = "google/gemini-2.0-flash-001"


# ── Preset prompt templates ────────────────────────────────────────────────────

PRESET_PROMPTS: dict = {
    "coo_name":       "Name of the Chief Operating Officer, Firm Administrator, Director of Operations, or equivalent operations leader.",
    "coo_email":      "Email address of the Chief Operating Officer, Firm Administrator, or Director of Operations.",
    "coo_phone":      "Phone number of the Chief Operating Officer, Firm Administrator, or Director of Operations.",
    "coo_linkedin":   "LinkedIn profile URL of the Chief Operating Officer, Firm Administrator, or Director of Operations.",
    "mp_name":        "Name of the Managing Partner or Senior Partner.",
    "mp_email":       "Email address of the Managing Partner or Senior Partner.",
    "attorney_count": "Total number of attorneys or lawyers at the firm. Return just the number.",
    "website":        "Official website URL of the firm.",
    "offices":        "Office locations (cities). Return as a comma-separated list.",
    "practice_areas": "Main practice areas or specializations. Return as a comma-separated list.",
}

# Keywords used to build a broad but relevant search query per data point type
_QUERY_HINTS: dict = {
    "coo_name":       "operations director administrator leadership",
    "coo_email":      "operations director contact email",
    "coo_phone":      "operations director contact phone",
    "coo_linkedin":   "operations director linkedin",
    "mp_name":        "managing partner leadership",
    "mp_email":       "managing partner contact email",
    "attorney_count": "attorneys lawyers headcount size",
    "website":        "official website",
    "offices":        "offices locations cities",
    "practice_areas": "practice areas specialization",
}


# ── Tavily search ──────────────────────────────────────────────────────────────

async def _search(
    query: str,
    client: httpx.AsyncClient,
    include_domains: Optional[List[str]] = None,
    max_results: int = 5,
) -> List[dict]:
    try:
        payload: dict = {
            "api_key":            TAVILY_API_KEY,
            "query":              query,
            "search_depth":       "basic",
            "max_results":        max_results,
            "include_raw_content": True,
        }
        if include_domains:
            payload["include_domains"] = include_domains
        resp = await client.post(TAVILY_URL, json=payload, timeout=20)
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception:
        return []


def _build_query(firm_name: str, data_points: List[dict]) -> str:
    """Derive a single broad search query that covers all requested data points."""
    hints = set()
    for dp in data_points:
        preset = dp.get("preset") or ""
        if preset in _QUERY_HINTS:
            hints.update(_QUERY_HINTS[preset].split())
        else:
            # Pull first 3 meaningful words from freeform prompt
            words = [w for w in (dp.get("prompt") or "").split() if len(w) > 3][:3]
            hints.update(words)

    # Cap to 6 hint words so the query stays focused
    hint_str = " ".join(list(hints)[:6])
    return f"{firm_name} {hint_str}".strip()


# ── Gemini batch extraction ────────────────────────────────────────────────────

def _build_context(search_results: List[dict], max_chars_per_result: int = 1800) -> str:
    parts = []
    for r in search_results[:4]:
        content = (r.get("raw_content") or r.get("content") or "")[:max_chars_per_result]
        parts.append(f"Source: {r.get('url', '')}\nTitle: {r.get('title', '')}\n{content}")
    return "\n\n---\n\n".join(parts)


async def _extract_batch(
    firm_name: str,
    search_results: List[dict],
    data_points: List[dict],   # [{column, prompt_text}]
    client: httpx.AsyncClient,
) -> dict:
    """
    ONE Gemini call to extract all requested data points.
    Returns {column_name: {value, status, source_url}, ...}
    """
    if not search_results:
        return {
            dp["column"]: {"value": None, "status": "not_found", "source_url": None}
            for dp in data_points
        }

    context = _build_context(search_results)

    fields_block = "\n".join(
        f'- "{dp["column"]}": {dp["prompt_text"]}'
        for dp in data_points
    )

    system_prompt = (
        "You are a precise data extraction assistant for law firm research.\n"
        "Extract the requested fields from the search results provided.\n\n"
        "For each field return:\n"
        '  "value"      : the extracted string, or null\n'
        '  "status"     : "found" | "not_sure" | "not_available" | "not_found"\n'
        '  "source_url" : URL where found, or null\n\n'
        "Status guide:\n"
        "  found        – confident answer in the sources\n"
        "  not_sure     – best guess, low confidence\n"
        "  not_available – information clearly not made public by this firm\n"
        "  not_found    – not in these sources (may exist elsewhere)\n\n"
        "Respond ONLY with a JSON object keyed by field name. No other text."
    )

    user_prompt = (
        f"Firm: {firm_name}\n\n"
        f"Fields to extract:\n{fields_block}\n\n"
        f"Search results:\n{context}\n\n"
        "Respond with JSON only."
    )

    try:
        resp = await client.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type":  "application/json",
                "HTTP-Referer":  "https://lucioai.com",
                "X-Title":       "Lucio Enrichment",
            },
            json={
                "model":       MODEL,
                "messages":    [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                "temperature": 0.1,
                "max_tokens":  512,   # higher than single-point mode to fit all fields
            },
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```(?:json)?\n?", "", raw).strip()
        raw = re.sub(r"\n?```$", "", raw).strip()
        parsed = json.loads(raw)

        # Normalise: ensure every requested column is present
        result = {}
        for dp in data_points:
            col = dp["column"]
            entry = parsed.get(col, {})
            if isinstance(entry, dict):
                result[col] = {
                    "value":      entry.get("value"),
                    "status":     entry.get("status", "not_found"),
                    "source_url": entry.get("source_url"),
                }
            else:
                # Model returned a bare string instead of an object
                result[col] = {"value": str(entry) if entry else None, "status": "not_sure", "source_url": None}
        return result

    except json.JSONDecodeError:
        return {dp["column"]: {"value": None, "status": "not_found", "source_url": None} for dp in data_points}
    except Exception:
        return {dp["column"]: {"value": None, "status": "not_found", "source_url": None} for dp in data_points}


# ── Main per-firm entry point ──────────────────────────────────────────────────

async def enrich_row(
    firm_name: str,
    website: Optional[str],
    data_points: List[dict],   # [{column, prompt, preset}]
    client: httpx.AsyncClient,
) -> List[dict]:
    """
    Enrich all data points for one firm using at most 2 searches + 2 LLM calls.
    Returns [{column, value, status, source_url}, ...]
    """
    # Resolve prompt text for every data point upfront
    resolved = [
        {
            "column":      dp.get("column", ""),
            "prompt_text": PRESET_PROMPTS.get(dp.get("preset") or "", "") or dp.get("prompt", ""),
        }
        for dp in data_points
        if dp.get("column")
    ]

    if not resolved:
        return []

    # ── Pass 1: domain-scoped search ──────────────────────────────────────────
    query   = _build_query(firm_name, data_points)
    domains = [website] if website else None
    results = await _search(query, client, include_domains=domains)
    batch1  = await _extract_batch(firm_name, results, resolved, client)

    # ── Pass 2: open-web fallback for anything still "not_found" ─────────────
    still_missing = [dp for dp in resolved if batch1.get(dp["column"], {}).get("status") == "not_found"]

    if still_missing:
        fallback_query   = _build_query(f"{firm_name} law firm", still_missing)
        fallback_results = await _search(fallback_query, client)   # no domain filter
        if fallback_results:
            batch2 = await _extract_batch(firm_name, fallback_results, still_missing, client)
            # Merge: update only the fields we retried
            for dp in still_missing:
                col = dp["column"]
                if batch2.get(col, {}).get("status") != "not_found":
                    batch1[col] = batch2[col]

    return [{"column": col, **info} for col, info in batch1.items()]
