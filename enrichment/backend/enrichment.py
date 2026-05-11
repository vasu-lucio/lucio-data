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
    "coo_name":       "Name of the Chief Operating Officer, Firm Administrator, Executive Director, or Director of Operations.",
    "coo_email":      "Email address of the Chief Operating Officer, Firm Administrator, or Executive Director.",
    "coo_phone":      "Direct phone number of the Chief Operating Officer, Firm Administrator, or Executive Director.",
    "coo_linkedin":   "LinkedIn profile URL of the Chief Operating Officer, Firm Administrator, or Executive Director.",
    "mp_name":        "Name of the Managing Partner, Managing Shareholder, Senior Partner, or firm CEO/head.",
    "mp_email":       "Email address of the Managing Partner, Managing Shareholder, or Senior Partner.",
    "mp_phone":       "Direct phone number of the Managing Partner, Managing Shareholder, or Senior Partner.",
    "mp_linkedin":    "LinkedIn profile URL of the Managing Partner, Managing Shareholder, or Senior Partner.",
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
                value  = entry.get("value")
                status = entry.get("status", "not_found")
                # Reject masked/redacted values (e.g. "m*****@domain.com")
                if value and "*" in str(value):
                    value, status = None, "not_found"
                # Reject initials-only names (e.g. "K. S." or "J. L.")
                if value and dp.get("preset", "").endswith("_name"):
                    import re as _re
                    if _re.fullmatch(r"([A-Z]\.\s*)+", str(value).strip()):
                        value, status = None, "not_found"
                result[col] = {
                    "value":      value,
                    "status":     status,
                    "source_url": entry.get("source_url"),
                }
            else:
                value = str(entry) if entry else None
                if value and "*" in value:
                    value = None
                result[col] = {"value": value, "status": "not_sure" if value else "not_found", "source_url": None}
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
        roles_str = " OR ".join(f'"{r}"' for r in roles)

        # Pass 1: find names from the firm's leadership/people page
        q1 = (
            f'"{firm_name}" ({roles_str}) (site:{domain} OR leadership OR team OR people OR about)'
            if domain else
            f'"{firm_name}" law firm ({roles_str}) leadership team people'
        )
        results1 = await _search(q1, client, include_domains=domains_list, max_results=5)
        if not results1:
            results1 = await _search(f'"{firm_name}" law firm {" ".join(roles)}', client, max_results=5)

        batch = await _extract_batch(firm_name, results1, leadership_dps, client)

        # Pass 2: for each name we found, search their individual profile page for email/phone
        # e.g. "Gary Rosen" "Becker Poliakoff" email phone → hits their bio page directly
        contact_dps = [dp for dp in leadership_dps
                       if any(x in dp["preset"] for x in ("email", "phone", "linkedin"))
                       and batch.get(dp["column"], {}).get("status") in ("not_found", "not_available")]

        if contact_dps:
            # Collect names we already found to use in the targeted query
            found_names = []
            for role_prefix in ("mp_", "coo_", "cio_"):
                name_col = next((dp["column"] for dp in leadership_dps if dp["preset"] == f"{role_prefix}name"), None)
                if name_col and batch.get(name_col, {}).get("status") == "found":
                    found_names.append(batch[name_col]["value"])

            firm_short = firm_name.replace(" LLP", "").replace(" LLC", "").replace(" PLLC", "").replace(" P.A.", "").strip()
            if found_names:
                for name in found_names:
                    q2 = f'"{name}" "{firm_short}" email phone profile'
                    r2 = await _search(q2, client, include_domains=domains_list, max_results=4)
                    if r2:
                        b2 = await _extract_batch(firm_name, r2, contact_dps, client)
                        for dp in contact_dps:
                            col = dp["column"]
                            if b2.get(col, {}).get("status") == "found":
                                batch[col] = b2[col]
            else:
                # No names found yet — do a broader contact search
                q2 = f'"{firm_name}" law firm {" ".join(roles)} email phone contact'
                r2 = await _search(q2, client, max_results=5)
                if r2:
                    b2 = await _extract_batch(firm_name, r2, contact_dps, client)
                    for dp in contact_dps:
                        col = dp["column"]
                        if b2.get(col, {}).get("status") == "found":
                            batch[col] = b2[col]

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
