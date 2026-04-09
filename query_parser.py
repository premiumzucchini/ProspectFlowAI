"""
query_parser.py
Converts a natural language prospecting request into:
  - Structured filters (titles, location, size)
  - A list of target company domains for Hunter.io to search
"""

import json
import math
from anthropic import Anthropic

client = Anthropic()


def parse_prospect_query(query: str) -> dict:
    """
    Parse a natural language query into filters + target company domains.

    Returns a dict with keys:
      - keywords: str
      - person_titles: list[str]
      - employee_min: int | None
      - employee_max: int | None
      - locations: list[str]
      - count: int
      - target_domains: list[str]  ← used by Hunter.io
    """

    prompt = f"""You are a GTM analyst. Parse this sales prospecting request and generate target company domains.

Query: "{query}"

Return ONLY a valid JSON object with these exact fields:
{{
  "keywords": "<industry + role summary, 3-5 words>",
  "person_titles": ["<title1>", "<title2>", "<title3>", "<title4>"],
  "employee_min": <integer or null>,
  "employee_max": <integer or null>,
  "locations": ["<country or region>"],
  "count": <integer, default 25, max 100>,
  "target_domains": ["<domain1.com>", "<domain2.com>", ...]
}}

Rules for target_domains:
- Generate real company domains that match the industry, size, and location in the query
- Include enough domains so that (domains × ~4 contacts each) ≥ count requested
- Use actual known company domains (e.g. crowdstrike.com, sentinelone.com)
- Prioritize companies that match the employee size range
- Do NOT make up domains — only use real companies you know exist

Rules for person_titles:
- Include 4-6 seniority levels relevant to the query (e.g. CISO, VP Security, Director of Security, Security Manager, Head of InfoSec)

Return ONLY valid JSON, no explanation."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    filters = json.loads(raw)

    # Ensure we have enough domains to hit the count target
    count = int(filters.get("count", 25))
    domains = filters.get("target_domains", [])
    needed_domains = math.ceil(count / 4)

    if len(domains) < needed_domains:
        domains = _expand_domains(filters, needed_domains - len(domains), domains)
        filters["target_domains"] = domains

    return filters


def _expand_domains(filters: dict, more_needed: int, existing: list) -> list:
    """Ask Claude for additional domains if the first pass didn't generate enough."""
    prompt = f"""Generate {more_needed} more real company domains for this prospecting target:
Industry: {filters.get('keywords', '')}
Employee range: {filters.get('employee_min', 'any')} - {filters.get('employee_max', 'any')}
Location: {filters.get('locations', [])}
Already have: {existing}

Return ONLY a JSON array of domain strings, e.g. ["example.com", "other.com"]
Only use real companies you know exist. No made-up domains."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    new_domains = json.loads(raw)
    return existing + [d for d in new_domains if d not in existing]
