"""
Enrichment engine — Tavily search + Gemini extraction.

Flow per firm:
  1. Leadership pack (COO / MP / CIO): ONE targeted Tavily search for the
     firm's people/leadership page, then ONE Gemini extraction call for all.
  2. Firm-info fields: ONE Tavily search, ONE Gemini extraction.
  3. Custom freeform fields: individual queries.
  4. Fallback open-web search for anything still "not_found".
"""

import asyncio
import json
import os
import re
from typing import Any, List, Optional

import httpx

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
TAVILY_API_KEY     = os.getenv("TAVILY_API_KEY", "")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TAVILY_URL     = "https://api.tavily.com/search"

MODEL = "google/gemini-2.0-flash-001"


def _or_headers() -> dict:
    """Build OpenRouter headers dynamically so the API key is always current."""
    return {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://lucioai.com",
        "X-Title":       "Lucio Enrichment",
    }


# ── Preset definitions ─────────────────────────────────────────────────────────

PRESET_PROMPTS: dict = {
    "coo_name":       "Name of the Chief Operating Officer, Firm Administrator, or Director of Operations.",
    "coo_email":      "Email address of the Chief Operating Officer or Firm Administrator.",
    "coo_phone":      "Phone number of the Chief Operating Officer or Firm Administrator.",
    "coo_linkedin":   "LinkedIn profile URL of the Chief Operating Officer or Firm Administrator.",
    "mp_name":        "Name of the Managing Partner or Senior Partner.",
    "mp_email":       "Email address of the Managing Partner or Senior Partner.",
    "mp_phone":       "Phone number of the Managing Partner or Senior Partner.",
    "mp_linkedin":    "LinkedIn profile URL of the Managing Partner or Senior Partner.",
    "cio_name":       "Name of the Chief Information Officer or Director of Technology.",
    "cio_email":      "Email address of the Chief Information Officer or Director of Technology.",
    "cio_phone":      "Phone number of the Chief Information Officer or Director of Technology.",
    "cio_linkedin":   "LinkedIn profile URL of the Chief Information Officer or Director of Technology.",
    "attorney_count": "Total number of attorneys or lawyers at the firm. Return just the number.",
    "website":        "Official website URL of the firm.",
    "offices":        "Office locations (cities). Return as a comma-separated list.",
    "practice_areas": "Main practice areas or specializations. Return as a comma-separated list.",
}

LEADERSHIP_PRESETS = {
    "coo_name", "coo_email", "coo_phone", "coo_linkedin",
    "mp_name",  "mp_email",  "mp_phone",  "mp_linkedin",
    "cio_name", "cio_email", "cio_phone", "cio_linkedin",
}

FIRM_INFO_PRESETS = {"attorney_count", "website", "offices", "practice_areas"}


# ── Tavily search ──────────────────────────────────────────────────────────────

async def _search(
    query: str,
    client: httpx.AsyncClient,
    include_domains: Optional[List[str]] = None,
    max_results: int = 5,
) -> List[dict]:
    try:
        payload: dict = {
            "api_key":             TAVILY_API_KEY,
            "query":               query,
            "search_depth":        "advanced",
            "max_results":         max_results,
            "include_raw_content": True,
        }
        if include_domains:
            payload["include_domains"] = include_domains
        resp = await client.post(TAVILY_URL, json=payload, timeout=25)
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception:
        return []


# ── Gemini batch extraction ────────────────────────────────────────────────────

def _build_context(results: List[dict], max_chars: int = 2500) -> str:
    parts = []
    for r in results[:5]:
        content = (r.get("raw_content") or r.get("content") or "")[:max_chars]
        parts.append(f"URL: {r.get('url', '')}\nTitle: {r.get('title', '')}\n{content}")
    return "\n\n---\n\n".join(parts)


async def _extract_batch(
    firm_name: str,
    search_results: List[dict],
    data_points: List[dict],
    client: httpx.AsyncClient,
) -> dict:
    if not search_results:
        return {dp["column"]: {"value": None, "status": "not_found", "source_url": None}
                for dp in data_points}

    context     = _build_context(search_results)
    fields_block = "\n".join(f'- "{dp["column"]}": {dp["prompt_text"]}' for dp in data_points)

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
        "  not_available – information clearly not made public\n"
        "  not_found    – not in these sources\n\n"
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
            headers=_or_headers(),
            json={
                "model":       MODEL,
                "messages":    [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                "temperature": 0.1,
                "max_tokens":  600,
            },
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```(?:json)?\n?", "", raw).strip()
        raw = re.sub(r"\n?```$", "", raw).strip()
        parsed = json.loads(raw)

        result = {}
        for dp in data_points:
            col   = dp["column"]
            entry = parsed.get(col, {})
            if isinstance(entry, dict):
                result[col] = {
                    "value":      entry.get("value"),
                    "status":     entry.get("status", "not_found"),
                    "source_url": entry.get("source_url"),
                }
            else:
                result[col] = {
                    "value":      str(entry) if entry else None,
                    "status":     "not_sure",
                    "source_url": None,
                }
        return result

    except (json.JSONDecodeError, KeyError, Exception):
        return {dp["column"]: {"value": None, "status": "not_found", "source_url": None}
                for dp in data_points}


# ── Main per-firm entry point ──────────────────────────────────────────────────

async def enrich_row(
    firm_name: str,
    website: Optional[str],
    data_points: List[dict],
    client: httpx.AsyncClient,
) -> List[dict]:
    """
    Enrich all data points for one firm.
    Leadership fields (COO/MP/CIO) are bundled into one targeted search.
    """
    resolved = [
        {
            "column":      dp.get("column", ""),
            "prompt_text": PRESET_PROMPTS.get(dp.get("preset") or "", "") or dp.get("prompt", ""),
            "preset":      dp.get("preset") or "",
        }
        for dp in data_points
        if dp.get("column")
    ]
    if not resolved:
        return []

    leadership_dps = [dp for dp in resolved if dp["preset"] in LEADERSHIP_PRESETS]
    firminfo_dps   = [dp for dp in resolved if dp["preset"] in FIRM_INFO_PRESETS]
    custom_dps     = [dp for dp in resolved if dp["preset"] not in LEADERSHIP_PRESETS | FIRM_INFO_PRESETS]

    domain       = website.replace("https://", "").replace("http://", "").rstrip("/") if website else None
    domains_list = [website] if website else None
    results: dict = {}

    # ── Leadership pack ───────────────────────────────────────────────────────
    if leadership_dps:
        roles = []
        if any(dp["preset"].startswith("mp_")  for dp in leadership_dps): roles.append("Managing Partner")
        if any(dp["preset"].startswith("coo_") for dp in leadership_dps): roles.append("COO OR Firm Administrator OR Director of Operations")
        if any(dp["preset"].startswith("cio_") for dp in leadership_dps): roles.append("CIO OR Director of Technology")

        # Target the firm's own people/about/leadership page first
        roles_str = " OR ".join(f'"{r}"' for r in roles)
        q_site = (
            f'"{firm_name}" ({roles_str}) (site:{domain} OR "about" OR "leadership" OR "team" OR "people")'
            if domain else
            f'"{firm_name}" law firm ({roles_str}) leadership team'
        )
        results1 = await _search(q_site, client, include_domains=domains_list, max_results=5)

        # Fallback: open web if we didn't find much
        if not results1:
            results1 = await _search(
                f'"{firm_name}" law firm {" ".join(roles)} contact',
                client, max_results=5
            )

        batch = await _extract_batch(firm_name, results1, leadership_dps, client)

        # Second pass: open web for any still missing
        still_missing = [dp for dp in leadership_dps
                         if batch.get(dp["column"], {}).get("status") == "not_found"]
        if still_missing:
            results2 = await _search(
                f'"{firm_name}" law firm {" ".join(roles)} email contact directory',
                client, max_results=5
            )
            if results2:
                batch2 = await _extract_batch(firm_name, results2, still_missing, client)
                for dp in still_missing:
                    if batch2.get(dp["column"], {}).get("status") != "not_found":
                        batch[dp["column"]] = batch2[dp["column"]]

        results.update(batch)

    # ── Firm info ─────────────────────────────────────────────────────────────
    if firminfo_dps:
        q = f'"{firm_name}" law firm ' + " ".join(
            {"attorney_count": "attorneys size headcount",
             "website": "official website",
             "offices": "office locations cities",
             "practice_areas": "practice areas specialization"}.get(dp["preset"], dp["prompt_text"])
            for dp in firminfo_dps
        )
        r = await _search(q, client, include_domains=domains_list, max_results=5)
        if not r:
            r = await _search(q, client, max_results=5)
        batch = await _extract_batch(firm_name, r, firminfo_dps, client)
        results.update(batch)

    # ── Custom fields ─────────────────────────────────────────────────────────
    for dp in custom_dps:
        q = f'"{firm_name}" law firm {dp["prompt_text"]}'
        r = await _search(q, client, include_domains=domains_list, max_results=5)
        if not r:
            r = await _search(q, client, max_results=5)
        batch = await _extract_batch(firm_name, r, [dp], client)
        results.update(batch)

    return [{"column": col, **info} for col, info in results.items()]
