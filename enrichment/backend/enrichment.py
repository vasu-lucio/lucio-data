"""
Enrichment engine — Perplexity Sonar for research, Gemini for extraction.

Flow per firm:
  1. Group data points into research topics (leadership pack, firm info, custom)
  2. ONE Perplexity Sonar call per group  →  natural-language research answer
  3. ONE Gemini call  →  extract ALL fields as structured JSON

Leadership pack (COO / Managing Partner / CIO) are always bundled into one
targeted query so we find the firm's actual people page rather than scattered
snippets.
"""

import asyncio
import json
import os
import re
from typing import Any, List, Optional

import httpx

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

RESEARCH_MODEL  = "perplexity/sonar"           # web search built-in
EXTRACT_MODEL   = "google/gemini-2.0-flash-001" # structured JSON extraction

HEADERS = {
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

# Presets that belong to the leadership research bundle
LEADERSHIP_PRESETS = {
    "coo_name", "coo_email", "coo_phone", "coo_linkedin",
    "mp_name",  "mp_email",  "mp_phone",  "mp_linkedin",
    "cio_name", "cio_email", "cio_phone", "cio_linkedin",
}

FIRM_INFO_PRESETS = {"attorney_count", "website", "offices", "practice_areas"}


# ── Perplexity research call ───────────────────────────────────────────────────

async def _research(
    question: str,
    client: httpx.AsyncClient,
) -> str:
    """
    Ask Perplexity Sonar a targeted research question.
    Returns the natural-language answer (with citations stripped).
    """
    try:
        resp = await client.post(
            OPENROUTER_URL,
            headers=HEADERS,
            json={
                "model": RESEARCH_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a legal industry researcher. "
                            "Answer with specific facts, names, and contact details. "
                            "Be concise but complete. Include sources where possible."
                        ),
                    },
                    {"role": "user", "content": question},
                ],
                "temperature": 0.1,
                "max_tokens": 1024,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[research failed: {e}]"


# ── Gemini extraction call ─────────────────────────────────────────────────────

async def _extract(
    firm_name: str,
    research_text: str,
    data_points: List[dict],  # [{column, prompt_text}]
    client: httpx.AsyncClient,
) -> dict:
    """
    One Gemini call: extract all requested fields from research text as JSON.
    Returns {column_name: {value, status, source_url}, ...}
    """
    if not research_text or research_text.startswith("[research failed"):
        return {
            dp["column"]: {"value": None, "status": "not_found", "source_url": None}
            for dp in data_points
        }

    fields_block = "\n".join(
        f'- "{dp["column"]}": {dp["prompt_text"]}'
        for dp in data_points
    )

    system_prompt = (
        "You are a precise data extraction assistant for law firm research.\n"
        "Extract the requested fields from the research text provided.\n\n"
        "For each field return:\n"
        '  "value"      : the extracted string, or null\n'
        '  "status"     : "found" | "not_sure" | "not_available" | "not_found"\n'
        '  "source_url" : URL where found, or null\n\n'
        "Status guide:\n"
        "  found        – confident answer in the research\n"
        "  not_sure     – best guess, low confidence\n"
        "  not_available – information clearly not made public by this firm\n"
        "  not_found    – not mentioned in the research\n\n"
        "Respond ONLY with a JSON object keyed by field name. No other text."
    )

    user_prompt = (
        f"Firm: {firm_name}\n\n"
        f"Fields to extract:\n{fields_block}\n\n"
        f"Research:\n{research_text}\n\n"
        "Respond with JSON only."
    )

    try:
        resp = await client.post(
            OPENROUTER_URL,
            headers=HEADERS,
            json={
                "model": EXTRACT_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 512,
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
        return {
            dp["column"]: {"value": None, "status": "not_found", "source_url": None}
            for dp in data_points
        }


# ── Main per-firm entry point ──────────────────────────────────────────────────

async def enrich_row(
    firm_name: str,
    website: Optional[str],
    data_points: List[dict],   # [{column, prompt, preset}]
    client: httpx.AsyncClient,
) -> List[dict]:
    """
    Enrich all data points for one firm.
    Leadership fields (COO / MP / CIO) are bundled into one targeted search.
    Other fields are grouped into a second search.
    Returns [{column, value, status, source_url}, ...]
    """
    # Resolve prompt text for every data point
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

    # Split into groups: leadership, firm-info, custom
    leadership_dps = [dp for dp in resolved if dp["preset"] in LEADERSHIP_PRESETS]
    firminfo_dps   = [dp for dp in resolved if dp["preset"] in FIRM_INFO_PRESETS]
    custom_dps     = [dp for dp in resolved if dp["preset"] not in LEADERSHIP_PRESETS | FIRM_INFO_PRESETS]

    results: dict = {}

    site_hint = f" Their website is {website}." if website else ""

    # ── Leadership bundle ─────────────────────────────────────────────────────
    if leadership_dps:
        roles_wanted = []
        if any(dp["preset"].startswith("mp_")  for dp in leadership_dps):
            roles_wanted.append("Managing Partner (name, email, phone, LinkedIn)")
        if any(dp["preset"].startswith("coo_") for dp in leadership_dps):
            roles_wanted.append("COO / Firm Administrator / Director of Operations (name, email, phone, LinkedIn)")
        if any(dp["preset"].startswith("cio_") for dp in leadership_dps):
            roles_wanted.append("CIO / Director of Technology / IT Director (name, email, phone, LinkedIn)")

        q = (
            f"Find the following leadership contacts at {firm_name} law firm:{site_hint} "
            + ", ".join(roles_wanted)
            + ". Search their website's About, Leadership, Team, or People page. "
            "Include full names, direct email addresses, phone numbers, and LinkedIn URLs where available."
        )
        research = await _research(q, client)
        batch = await _extract(firm_name, research, leadership_dps, client)
        results.update(batch)

    # ── Firm info bundle ──────────────────────────────────────────────────────
    if firminfo_dps:
        fields = ", ".join(dp["prompt_text"] for dp in firminfo_dps)
        q = (
            f"For {firm_name} law firm:{site_hint} find: {fields}. "
            "Use their official website or authoritative legal directories."
        )
        research = await _research(q, client)
        batch = await _extract(firm_name, research, firminfo_dps, client)
        results.update(batch)

    # ── Custom fields (one query each) ────────────────────────────────────────
    for dp in custom_dps:
        q = f"For {firm_name} law firm:{site_hint} {dp['prompt_text']}"
        research = await _research(q, client)
        batch = await _extract(firm_name, research, [dp], client)
        results.update(batch)

    return [{"column": col, **info} for col, info in results.items()]
