"""
hubspot_client.py
Creates contacts in HubSpot CRM via the Contacts v3 API.

Docs: https://developers.hubspot.com/docs/api/crm/contacts
Auth: HubSpot Private App token (Bearer token)
"""

import os
import time
import requests

HUBSPOT_BASE_URL = "https://api.hubapi.com"

# HubSpot free tier rate limit: 100 req/10s — 0.1s sleep is safe
RATE_LIMIT_SLEEP = 0.12


def _get_headers() -> dict:
    token = os.getenv("HUBSPOT_API_KEY")
    if not token:
        raise ValueError("HUBSPOT_API_KEY not set in environment")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def create_contact(prospect: dict) -> dict:
    """
    Create a single contact in HubSpot.

    Returns:
      {"status": "created", "id": "..."}  — new contact
      {"status": "exists", "email": "..."} — contact already in HubSpot (409)
      {"status": "error", "error": "..."}  — unexpected error
    """
    properties = {
        "firstname": prospect.get("first_name", ""),
        "lastname": prospect.get("last_name", ""),
        "email": prospect.get("email", ""),
        "company": prospect.get("company", ""),
    }

    # Skip contacts with no email — HubSpot requires it for dedup
    if not properties["email"]:
        return {"status": "skipped", "reason": "no email"}

    resp = requests.post(
        f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts",
        json={"properties": properties},
        headers=_get_headers(),
        timeout=15,
    )

    if resp.status_code == 409:
        # Contact already exists — not an error, just note it
        return {"status": "exists", "email": properties["email"]}

    if not resp.ok:
        return {"status": "error", "error": f"{resp.status_code}: {resp.text[:200]}"}

    return {"status": "created", "id": resp.json().get("id", "")}


def create_contacts_batch(prospects: list[dict]) -> dict:
    """
    Create all accepted prospects in HubSpot, one by one with rate-limit sleep.

    Returns a summary dict:
      created: int
      skipped_existing: int
      skipped_no_email: int
      errors: list[dict]
    """
    created = 0
    skipped_existing = 0
    skipped_no_email = 0
    errors = []

    for prospect in prospects:
        result = create_contact(prospect)
        status = result.get("status")

        if status == "created":
            created += 1
        elif status == "exists":
            skipped_existing += 1
        elif status == "skipped":
            skipped_no_email += 1
        elif status == "error":
            errors.append(
                {
                    "email": prospect.get("email", "unknown"),
                    "name": f"{prospect.get('first_name', '')} {prospect.get('last_name', '')}".strip(),
                    "error": result.get("error"),
                }
            )

        time.sleep(RATE_LIMIT_SLEEP)

    return {
        "created": created,
        "skipped_existing": skipped_existing,
        "skipped_no_email": skipped_no_email,
        "errors": errors,
    }
