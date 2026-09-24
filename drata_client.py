"""
Thin client for Drata's Public API (v2), scoped to what this app needs:
creating policies, mapping controls to a policy, and a couple of lookup
helpers (personnel-by-email, control search) used by the UI.

Reference (internal, as of Aug 2026):
  Base URL:      https://public-api.drata.com
  Auth header:   Authorization: Bearer <API_KEY>
  Create policy: POST /public/v2/policies
                   -> creates a Policy + an initial PolicyVersion in DRAFT
  Modify policy: PUT  /public/v2/policies/{policyId}
                   -> policy metadata + controlIds (REPLACES all existing
                      control assignments, not additive)
  Lifecycle:     POST /public/v2/policies/{policyId}/actions
                   -> not used here; policies are intentionally left DRAFT

Drata's Public API evolves. If any of these calls start failing with a
schema/validation error, check the live reference at
https://developers.drata.com/openapi/reference/v2/ and adjust the payload
builders below -- the shapes of the *known* fields (name, ownerId,
sourceType, content, contentFormat, renewalDate, controlIds, ...) come
from Drata's own engineering spec for this endpoint, but field names can
shift between API versions.
"""
from __future__ import annotations

import requests
from dataclasses import dataclass
from typing import Any, Optional


DEFAULT_BASE_URL = "https://public-api.drata.com"
DEFAULT_TIMEOUT = 30


class DrataAPIError(Exception):
    """Raised when the Drata API returns a non-2xx response."""

    def __init__(self, status_code: int, message: str, payload: Any = None):
        self.status_code = status_code
        self.payload = payload
        super().__init__(f"Drata API error {status_code}: {message}")


@dataclass
class DrataClient:
    api_key: str
    base_url: str = DEFAULT_BASE_URL

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}{path}"

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = self._url(path)
        resp = requests.request(
            method, url, headers=self._headers(), timeout=DEFAULT_TIMEOUT, **kwargs
        )
        if resp.status_code >= 400:
            try:
                body = resp.json()
                message = body.get("message", resp.text)
            except Exception:
                body = resp.text
                message = resp.text
            raise DrataAPIError(resp.status_code, message, body)
        return resp

    # ------------------------------------------------------------------
    # Connectivity
    # ------------------------------------------------------------------
    def test_connection(self) -> dict:
        """Hits a cheap, read-only v2 endpoint to confirm the key + base URL work."""
        resp = self._request("GET", "/public/v2/policies", params={"page": 1, "limit": 1})
        return resp.json()

    # ------------------------------------------------------------------
    # Policies
    # ------------------------------------------------------------------
    def create_policy(
        self,
        *,
        name: str,
        owner_id: str,
        content: str,
        description: Optional[str] = None,
        content_format: str = "PLAINTEXT",
        renewal_date: Optional[str] = None,
        requires_acknowledgement: bool = False,
        assigned_to: str = "NONE",
        group_ids: Optional[list] = None,
    ) -> dict:
        """
        Creates a policy authored directly (sourceType=BUILDER) from text
        supplied by the app (i.e. the contents of the per-policy .txt file).
        The resulting Policy + its initial PolicyVersion are created in
        DRAFT status by Drata -- no further call is needed to keep it a draft.
        """
        payload: dict[str, Any] = {
            "name": name,
            "ownerId": owner_id,
            "sourceType": "BUILDER",
            "content": content,
            "contentFormat": content_format,
        }
        if description:
            payload["description"] = description
        if renewal_date:
            payload["renewalDate"] = renewal_date
        if requires_acknowledgement:
            payload["requiresAcknowledgement"] = True
        if assigned_to and assigned_to != "NONE":
            payload["assignedTo"] = assigned_to
            if assigned_to == "GROUP" and group_ids:
                payload["groupIds"] = group_ids

        resp = self._request("POST", "/public/v2/policies", json=payload)
        return resp.json()

    def map_controls_to_policy(self, policy_id: str, control_ids: list) -> dict:
        """
        PUT /public/v2/policies/{policyId} with controlIds.
        NOTE: this REPLACES the full set of control assignments on the
        policy -- it is not additive. Since this app calls it once, right
        after creating a brand-new policy (which has no prior control
        assignments), that's exactly what we want here.
        """
        resp = self._request(
            "PUT",
            f"/public/v2/policies/{policy_id}",
            json={"controlIds": control_ids},
        )
        return resp.json()

    def get_policy(self, policy_id: str, expand: Optional[str] = None) -> dict:
        params = {"expand": expand} if expand else None
        resp = self._request("GET", f"/public/v2/policies/{policy_id}", params=params)
        return resp.json()

    # ------------------------------------------------------------------
    # Lookup helpers (best-effort; used only by the optional UI helpers)
    # ------------------------------------------------------------------
    def find_personnel_by_email(self, email: str, max_pages: int = 20) -> Optional[dict]:
        """
        Paginates through /public/personnel (v1) looking for a case-insensitive
        email match, since query-param filtering by email isn't confirmed
        across tenants. Returns the raw personnel record dict, or None.
        """
        email_lower = email.strip().lower()
        page = 1
        while page <= max_pages:
            resp = self._request(
                "GET", "/public/personnel", params={"page": page, "limit": 100}
            )
            body = resp.json()
            records = body.get("data", body) if isinstance(body, dict) else body
            if not records:
                break
            for record in records:
                rec_email = (record.get("email") or "").strip().lower()
                if rec_email == email_lower:
                    return record
            total_pages = body.get("totalPages") if isinstance(body, dict) else None
            page += 1
            if total_pages is not None and page > total_pages:
                break
            if len(records) < 100:
                break
        return None

    def search_controls(self, query: str, max_pages: int = 10) -> list:
        """
        Best-effort control search across v2 (falls back to v1) so the UI
        can help a user find a control's ID from its code/name. Returns a
        list of raw control records for the caller to render as a table.
        """
        query_lower = query.strip().lower()
        matches = []
        for path in ("/public/v2/controls", "/public/controls"):
            page = 1
            try:
                while page <= max_pages:
                    resp = self._request(
                        "GET", path, params={"page": page, "limit": 100}
                    )
                    body = resp.json()
                    records = body.get("data", body) if isinstance(body, dict) else body
                    if not records:
                        break
                    for record in records:
                        haystack = " ".join(
                            str(record.get(field, ""))
                            for field in ("code", "name", "id", "shortName")
                        ).lower()
                        if query_lower in haystack:
                            matches.append(record)
                    total_pages = body.get("totalPages") if isinstance(body, dict) else None
                    page += 1
                    if total_pages is not None and page > total_pages:
                        break
                    if len(records) < 100:
                        break
                if matches:
                    break
            except DrataAPIError:
                continue
        return matches
