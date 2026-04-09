"""
scorer.py
Scores each prospect 0-100 for fit against the original natural language query.

Batches prospects into groups of 15 per Claude call to minimize API cost/latency.
"""

import json
from anthropic import Anthropic

client = Anthropic()

BATCH_SIZE = 15


def score_prospects(prospects: list[dict], original_query: str, icp_profile: str = "") -> list[dict]:
    """
    Score all prospects for fit against the original query + optional ICP profile.

    Adds 'fit_score' (int 0-100) and 'fit_reason' (str) to each prospect dict.
    Returns the augmented list sorted by score descending.
    """
    scored = []

    for i in range(0, len(prospects), BATCH_SIZE):
        batch = prospects[i : i + BATCH_SIZE]
        scored.extend(_score_batch(batch, original_query, icp_profile=icp_profile, offset=i))

    scored.sort(key=lambda p: p["fit_score"], reverse=True)
    return scored


def _score_batch(prospects: list[dict], query: str, icp_profile: str = "", offset: int = 0) -> list[dict]:
    """Score a single batch of prospects via one Claude call."""

    lines = []
    for i, p in enumerate(prospects):
        name = f"{p['first_name']} {p['last_name']}".strip() or "Unknown"
        location = ", ".join(filter(None, [p.get("city"), p.get("state"), p.get("country")]))
        employees = p.get("num_employees", "unknown")
        lines.append(
            f"{i + 1}. {name} | {p['title']} | {p['company']} | "
            f"Employees: {employees} | Location: {location or 'unknown'}"
        )

    numbered_list = "\n".join(lines)

    icp_section = ""
    if icp_profile and icp_profile.strip():
        icp_section = f"""
ICP Profile (use this as the primary scoring rubric):
{icp_profile.strip()}

"""

    prompt = f"""You are a GTM analyst scoring sales prospect fit.

Original request: "{query}"
{icp_section}
Score each prospect below from 0 to 100 on how well they match.
{"Prioritize the ICP Profile criteria above the query when both are present." if icp_section else ""}
Consider: industry fit, company size, role/title relevance, location.

Prospects:
{numbered_list}

Return ONLY a JSON array, one object per prospect, in order:
[{{"index": 1, "score": 85, "reason": "CISO at 200-person US cybersecurity firm, exact match"}}, ...]

Rules:
- score 80-100: strong match on all criteria
- score 60-79: good match, minor gaps
- score 40-59: partial match (e.g. right industry, wrong size)
- score 0-39: poor fit
- reason: max 12 words explaining the score
- Return valid JSON only, no explanation"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    scores = json.loads(raw)
    score_map = {s["index"]: s for s in scores}

    result = []
    for i, prospect in enumerate(prospects):
        score_data = score_map.get(i + 1, {"score": 50, "reason": "Could not evaluate"})
        result.append(
            {
                **prospect,
                "fit_score": int(score_data.get("score", 50)),
                "fit_reason": score_data.get("reason", ""),
            }
        )

    return result
