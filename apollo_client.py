"""
apollo_client.py
Fetches prospects from Apollo.io's People Search API.

Apollo docs: https://apolloio.github.io/apollo-api-docs/#mixed-people-search
Endpoint: POST https://api.apollo.io/api/v1/mixed_people/search
"""

import os
import requests

APOLLO_BASE_URL = "https://api.apollo.io/v1"


def search_prospects(
    keywords: str = "",
    person_titles: list = None,
    organization_keyword_tags: list = None,
    employee_ranges: list = None,
    locations: list = None,
    count: int = 25,
) -> list[dict]:
    """
    Search Apollo for prospects matching the given filters.

    Returns a list of normalized prospect dicts with:
      first_name, last_name, email, company, title, linkedin_url, num_employees
    """
    api_key = os.getenv("APOLLO_API_KEY")
    if not api_key:
        raise ValueError("APOLLO_API_KEY not set in environment")

    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
        "Cache-Control": "no-cache",
    }

    # Apollo max per page is 100; cap at count
    per_page = min(count, 100)
    pages_needed = (count + per_page - 1) // per_page

    all_people = []

    for page in range(1, pages_needed + 1):
        payload = {
            "page": page,
            "per_page": per_page,
            # Only pull contacts with usable email status
            "contact_email_status_cd": ["verified", "likely to engage", "unavailable"],
        }

        if keywords:
            payload["q_keywords"] = keywords
        if person_titles:
            payload["person_titles"] = person_titles
        if organization_keyword_tags:
            payload["organization_keyword_tags"] = organization_keyword_tags
        if employee_ranges:
            payload["organization_num_employees_ranges"] = employee_ranges
        if locations:
            payload["person_locations"] = locations

        resp = requests.post(
            f"{APOLLO_BASE_URL}/mixed_people/search",
            json=payload,
            headers=headers,
            timeout=30,
        )

        if resp.status_code == 422:
            # Unprocessable filters — return empty rather than crash
            print(f"[Apollo] 422 Unprocessable: {resp.text}")
            return []

        resp.raise_for_status()
        data = resp.json()
        people = data.get("people", [])
        all_people.extend(people)

        # If Apollo returned fewer than requested, no point fetching more pages
        if len(people) < per_page:
            break

    return [_normalize(p) for p in all_people[:count]]


def _normalize(person: dict) -> dict:
    """Extract only the fields we care about from an Apollo person record."""
    org = person.get("organization") or {}

    # Apollo sometimes puts email at top level, sometimes inside contact
    email = person.get("email") or ""

    return {
        "first_name": person.get("first_name") or "",
        "last_name": person.get("last_name") or "",
        "email": email,
        "company": org.get("name") or person.get("organization_name") or "",
        "title": person.get("title") or "",
        "linkedin_url": person.get("linkedin_url") or "",
        "num_employees": org.get("estimated_num_employees") or "",
        "city": person.get("city") or "",
        "state": person.get("state") or "",
        "country": person.get("country") or "",
    }
