"""
hunter_client.py
Fetches prospects from Hunter.io's Domain Search API.

Hunter.io docs: https://hunter.io/api-documentation/v2#domain-search
Free tier: 25 searches/month | Starter: $49/mo for 500 searches
"""

import os
import time
import requests

HUNTER_BASE_URL = "https://api.hunter.io/v2"
RATE_LIMIT_SLEEP = 0.5


def search_prospects(
    target_domains: list,
    person_titles: list = None,
    count: int = 25,
    **kwargs,
) -> tuple[list[dict], list[dict]]:
    """
    Search Hunter.io for contacts across all target domains.

    Returns:
      (prospects, domain_log)
      - prospects: list of normalized contact dicts
      - domain_log: list of {domain, found, status} for UI display
    """
    api_key = os.getenv("HUNTER_API_KEY")
    if not api_key:
        raise ValueError("HUNTER_API_KEY not set in environment")

    per_domain = max(5, -(-count // max(len(target_domains), 1)))
    per_domain = min(per_domain, 20)

    all_prospects = []
    domain_log = []

    for domain in target_domains:
        if len(all_prospects) >= count * 2:  # gather extra for scoring headroom
            break

        contacts, status = _search_domain(api_key, domain, limit=per_domain)
        domain_log.append({"domain": domain, "found": len(contacts), "status": status})
        all_prospects.extend(contacts)
        time.sleep(RATE_LIMIT_SLEEP)

    # Prioritize contacts whose title matches requested titles
    if person_titles:
        all_prospects = _prioritize_by_title(all_prospects, person_titles)

    return all_prospects[:count], domain_log


def _search_domain(api_key: str, domain: str, limit: int = 10) -> tuple[list[dict], str]:
    """Pull contacts for a single domain. Returns (contacts, status_message)."""
    params = {
        "domain": domain,
        "limit": limit,
        "api_key": api_key,
        # No type filter — including both personal + generic emails for max coverage
    }

    try:
        resp = requests.get(
            f"{HUNTER_BASE_URL}/domain-search",
            params=params,
            timeout=15,
        )

        if resp.status_code == 429:
            time.sleep(5)
            return [], "rate_limited"

        if resp.status_code == 401:
            raise ValueError("Invalid Hunter API key — check HUNTER_API_KEY in .env")

        if not resp.ok:
            return [], f"error_{resp.status_code}"

        data = resp.json().get("data", {})
        org_name = data.get("organization") or domain.split(".")[0].title()
        emails = data.get("emails", [])

        results = []
        for e in emails:
            email = e.get("value") or ""
            if not email:
                continue

            results.append({
                "first_name": e.get("first_name") or "",
                "last_name": e.get("last_name") or "",
                "email": email,
                "company": org_name,
                "title": e.get("position") or "",
                "linkedin_url": e.get("linkedin") or "",
                "num_employees": "",
                "city": "",
                "state": "",
                "country": "",
                "domain": domain,
                "confidence": e.get("confidence", 0),
            })

        return results, "ok" if results else "no_contacts"

    except ValueError:
        raise
    except requests.RequestException as ex:
        return [], f"request_failed: {str(ex)[:60]}"


def _prioritize_by_title(prospects: list[dict], target_titles: list[str]) -> list[dict]:
    """Sort so title-matched contacts come first; keep the rest."""
    keywords = [t.lower() for t in target_titles]

    def matches(p):
        title = (p.get("title") or "").lower()
        return any(kw in title for kw in keywords)

    matched = [p for p in prospects if matches(p)]
    unmatched = [p for p in prospects if not matches(p)]
    return matched + unmatched
