"""
Thin client for Drata's Public API (v2), scoped to what this app needs:
creating policies, replacing an existing policy's content, mapping
controls to a policy, and a couple of lookup helpers (personnel-by-email,
control search) used by the UI.

Reference (internal design docs cross-checked against Drata's published v2
API reference at https://developers.drata.com/openapi/reference/v2/, and
several of the calls below corrected once or more against real 400s/live
tenant behavior -- see the per-method docstrings for exactly what changed
and why):
  Base URL:      https://public-api.drata.com
  Auth header:   Authorization: Bearer <API_KEY>
  Create policy: POST /public/v2/policies (multipart/form-data)
                   -> creates a Policy + an initial PolicyVersion in DRAFT
  Modify policy: PUT  /public/v2/policies/{policyId}
                   -> policy metadata (description, renewalDate, controlIds,
                      ...) -- NOT name (custom policies only -- Drata's
                      built-in template policies 400 on a name change) and
                      NOT ownerId (its own endpoint, below). controlIds
                      REPLACES all existing control assignments, not additive.
  Assign owner:  PUT  /public/v2/policies/{policyId}/owner  -> {"userId": ...}
  New version
  (replace an
  existing
  policy's
  content):     POST /public/v2/policies/{policyId}/policy-versions
                   (multipart/form-data) -> creates a new DRAFT PolicyVersion
                   under an EXISTING policy, same file/sourceType shape as
                   creating a brand-new policy, but `renewalDate` is REQUIRED
                   here (must be today or later) even though it's optional on
                   create. Used by the "replace an existing Drata policy by
                   name" flow instead of POST /public/v2/policies.
  Find by name:  GET  /public/v2/policies?name=... -- a PARTIAL-match filter,
                   so callers must still confirm an exact (case-insensitive)
                   name match client-side before treating a result as "the"
                   policy to replace.
  Lifecycle:     POST /public/v2/policies/{policyId}/actions
                   -> {"action": "SubmitForApproval"}  DRAFT -> NEEDS_APPROVAL
                   -> {"action": "OverrideApprove",
                       "overrideReason": "..."}         NEEDS_APPROVAL -> APPROVED
                   -> {"action": "Publish"}              APPROVED -> PUBLISHED
                   An API key has no reviewer identity, so "Approve" and
                   "RequestChanges" are never available to it -- the only
                   key-driven path from Draft to Published is
                   Submit -> OverrideApprove -> Publish. This action
                   endpoint's own response body carries {success, newStatus,
                   message}, but a live tenant proved `newStatus` can report
                   the *intended* transition before Drata actually commits
                   it (trusting it once produced a real "Action ... is not
                   available for the current resource state" 400) -- so
                   `newStatus` is shown only as a diagnostic, and the app
                   always confirms with a real poll of the dedicated
                   policy-versions endpoint (GET /public/v2/policies/{id}
                   /policy-versions?sort=createdAt&sortDir=DESC&size=1,
                   field `policyVersionStatus`) before moving to the next
                   step. That poll deliberately does NOT filter by
                   `current=true` -- also confirmed against a live tenant to
                   return zero records for a policy still mid-approval; see
                   get_current_policy_version()'s docstring. It does NOT
                   poll GET /public/v2/policies/{id} itself, which cannot
                   return version-workflow status at all either.

Drata's Public API evolves. If any of these calls start failing with a
schema/validation error, check the live reference at
https://developers.drata.com/openapi/reference/v2/ and adjust the payload
builders below -- the shapes of the *known* fields (name, ownerId,
sourceType, renewalDate, controlIds, ...) come from Drata's own
engineering spec for this endpoint, but field names can shift between
API versions, and the create-policy shape below has already been
corrected once against a real 400 -- see create_policy()'s docstring.

Owner lookup: GET /public/v2/users/email:{email} resolves any Drata user
by email (v2's /users/{id} route treats an "email:"-prefixed value as a
lookup key). Prefer this over /public/personnel for resolving a policy
owner -- Personnel records are workforce/HRIS-tracked and commonly don't
include admin/service accounts, which are Users but not Personnel.
"""
from __future__ import annotations

import time
import requests
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


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
    # Diagnostic trail from the most recent get_current_policy_version()
    # call: one dict per path attempted, e.g.
    #   {"path": "...", "status_code": 404, "error": "..."}                (request failed)
    #   {"path": "...", "status_code": 200, "response_shape": "...",
    #    "response_keys": [...], "record_count": 0}                        (request succeeded)
    # Exists so the app can show *exactly* what Drata's API actually
    # returned when polling comes up empty, instead of the caller having to
    # guess again -- this app has already shipped several guesses at the
    # shape of Drata's version-polling response (an assumed `latestVersion`
    # field, an assumed `policy-versions` envelope shape, an assumed
    # `current=true` filter) that each matched an internal doc or Drata's
    # own published API reference, and each one still missed something
    # real about a live tenant, so each round trip with the user to
    # discover that cost time this diagnostic exists to save going forward.
    last_version_lookup_debug: list = field(default_factory=list)

    def _headers(self, json_body: bool = True) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        # else: leave Content-Type unset for multipart/form-data requests --
        # `requests` sets it (with the correct boundary) automatically when
        # a `files=` kwarg is present, and setting it ourselves would break that.
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}{path}"

    def _request(
        self, method: str, path: str, *, multipart: bool = False, **kwargs
    ) -> requests.Response:
        url = self._url(path)
        resp = requests.request(
            method,
            url,
            headers=self._headers(json_body=not multipart),
            timeout=DEFAULT_TIMEOUT,
            **kwargs,
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
        description: str,
        file_bytes: bytes,
        file_name: str,
        file_content_type: str,
        renewal_date: Optional[str] = None,
        requires_acknowledgement: bool = False,
        assigned_to: str = "NONE",
        group_ids: Optional[list] = None,
    ) -> dict:
        """
        Creates a policy by uploading a document (sourceType=UPLOADED).

        NOTE ON HOW THIS SHAPE WAS DERIVED: the original design doc for this
        endpoint proposed a sourceType=BUILDER mode that took raw `content` /
        `contentFormat` fields directly in a JSON body -- that's what this
        method used to send. Against a real tenant, that came back as a 400:
            - `content` / `contentFormat`: "property ... should not exist"
              (rejected by whitelist validation -- not real fields)
            - `sourceType`: "must be one of the following values:
              UPLOADED,EXTERNAL" -- BUILDER isn't an accepted value
            - `description`: required (isNotEmpty), not optional as the
              design doc said

        So BUILDER apparently never shipped: policy creation is upload-only.
        This method now sends multipart/form-data with sourceType=UPLOADED
        and a `file` part, mirroring the sibling endpoint
        POST /policies/{policyId}/policy-versions, which Drata's own
        engineering notes describe as using "the same upload interceptor"
        as this one (multipart/form-data, file field named `file`). If the
        field name is still wrong for your tenant, the raw 400 body Drata
        returns will say so -- surface it and adjust the `files=` line below.

        The resulting Policy + its initial PolicyVersion are created in
        DRAFT status by Drata -- no further call is needed to keep it a draft.
        """
        data: dict[str, Any] = {
            "name": name,
            "ownerId": owner_id,
            "sourceType": "UPLOADED",
            "description": description,
        }
        if renewal_date:
            data["renewalDate"] = renewal_date
        if requires_acknowledgement:
            data["requiresAcknowledgement"] = "true"
        if assigned_to and assigned_to != "NONE":
            data["assignedTo"] = assigned_to
            if assigned_to == "GROUP" and group_ids:
                data["groupIds"] = group_ids

        files = {"file": (file_name, file_bytes, file_content_type)}

        resp = self._request(
            "POST", "/public/v2/policies", multipart=True, data=data, files=files
        )
        return resp.json()

    def map_controls_to_policy(self, policy_id: str, control_ids: list) -> dict:
        """
        PUT /public/v2/policies/{policyId} with controlIds.
        NOTE: this REPLACES the full set of control assignments on the
        policy -- it is not additive. Since this app calls it once, right
        after creating (or replacing) a policy, that's exactly what we want
        here.
        """
        resp = self._request(
            "PUT",
            f"/public/v2/policies/{policy_id}",
            json={"controlIds": control_ids},
        )
        return resp.json()

    def modify_policy(self, policy_id: str, **fields) -> dict:
        """
        PUT /public/v2/policies/{policyId} -- general policy metadata update,
        used by the "replace an existing policy" flow to update description
        and renewalDate on a policy that already exists (map_controls_to_policy
        above covers the controlIds-only case already in use elsewhere).

        Only keys with a non-None value are sent, so callers can pass
        `modify_policy(id, description=d, renewalDate=r, controlIds=None)`
        and have controlIds simply omitted rather than sent as null.

        Deliberately never accepts `name`: per Drata's published v2
        reference, this endpoint 400s on a name change for Drata's own
        built-in template policies, and this app only ever calls it for a
        policy it just matched BY that same name -- there's never a reason
        to also try to rename it. ownerId isn't accepted by this endpoint
        at all; use update_policy_owner() for that.
        """
        payload = {k: v for k, v in fields.items() if v is not None}
        if not payload:
            return {}
        resp = self._request("PUT", f"/public/v2/policies/{policy_id}", json=payload)
        try:
            return resp.json()
        except Exception:
            return {}

    def update_policy_owner(self, policy_id: str, owner_id) -> None:
        """
        PUT /public/v2/policies/{policyId}/owner -- {"userId": <int>}.
        Confirmed via Drata's published v2 reference to be a SEPARATE
        endpoint from modify_policy() above (that one doesn't accept
        ownerId at all). Returns 204 No Content on success, so there's
        nothing meaningful to hand back to the caller.
        """
        self._request(
            "PUT",
            f"/public/v2/policies/{policy_id}/owner",
            json={"userId": int(owner_id)},
        )

    def create_policy_version(
        self,
        policy_id: str,
        *,
        owner_id,
        renewal_date: str,
        file_bytes: bytes,
        file_name: str,
        file_content_type: str,
        description: Optional[str] = None,
        is_material_change: bool = True,
    ) -> dict:
        """
        POST /public/v2/policies/{policyId}/policy-versions -- uploads a new
        document as a brand-new DRAFT PolicyVersion under an EXISTING
        policy. Used by the "replace an existing Drata policy" flow instead
        of create_policy() (which makes a whole new Policy); the resulting
        DRAFT version then goes through the same Submit -> OverrideApprove
        -> Publish lifecycle as a newly created policy, driven by the same
        policy_id (the actions endpoint has no separate policyVersionId
        parameter, so it operates on the policy's current/latest version).

        `renewal_date` is REQUIRED by this endpoint (Drata's reference:
        "must be today or future date") -- unlike create_policy(), where
        it's optional. `is_material_change` defaults True: replacing a
        policy's entire content is a material change by any reasonable
        reading, and Drata's reference notes a material change always
        requires approval, which matches this app's existing lifecycle
        flow either way.
        """
        data: dict[str, Any] = {
            "sourceType": "UPLOADED",
            "ownerId": owner_id,
            "isMaterialChange": "true" if is_material_change else "false",
            "renewalDate": renewal_date,
        }
        if description:
            data["description"] = description
        if file_name:
            data["fileName"] = file_name

        files = {"file": (file_name, file_bytes, file_content_type)}

        resp = self._request(
            "POST",
            f"/public/v2/policies/{policy_id}/policy-versions",
            multipart=True,
            data=data,
            files=files,
        )
        return resp.json()

    def find_policy_by_name(
        self, name: str, max_pages: int = 5
    ) -> tuple[Optional[dict], list]:
        """
        Searches for an existing policy by name via
        GET /public/v2/policies?name=... -- confirmed via Drata's published
        v2 reference to be a PARTIAL-match filter ("name: string, <=191
        chars, partial match filter"), so this narrows the results to exact,
        case-insensitive full-name matches client-side before returning
        anything, since a "replace" flow should never guess between a
        merely-similar policy and the one actually meant.

        Returns (single_exact_match_or_None, all_exact_matches). If more
        than one exact match comes back (two policies really do share a
        name), the first element is None and the caller should treat that
        as "too ambiguous to replace automatically" rather than picking one.
        Paginates via the endpoint's cursor up to max_pages of 50 results
        each, which should be far more than any tenant has policies sharing
        one exact name.
        """
        name_stripped = (name or "").strip()
        if not name_stripped:
            return None, []

        exact_matches: list = []
        cursor: Optional[str] = None
        for _ in range(max_pages):
            params: dict[str, Any] = {"name": name_stripped, "size": 50}
            if cursor:
                params["cursor"] = cursor
            resp = self._request("GET", "/public/v2/policies", params=params)
            body = resp.json()
            records = body.get("data", []) if isinstance(body, dict) else []
            for record in records:
                if str(record.get("name", "")).strip().lower() == name_stripped.lower():
                    exact_matches.append(record)
            pagination = body.get("pagination", {}) if isinstance(body, dict) else {}
            cursor = pagination.get("cursor")
            if not cursor or not records:
                break

        if len(exact_matches) == 1:
            return exact_matches[0], exact_matches
        return None, exact_matches

    def list_all_policies(self, statuses: Optional[list] = None, max_pages: int = 40) -> list:
        """
        Returns every policy in the tenant via GET /public/v2/policies,
        paginating through the endpoint's cursor. Unlike find_policy_by_name,
        this does no name filtering -- callers get the raw records to
        filter/display however they like. Used by the "Activate/Publish
        Templates" tab to list every policy in the tenant (including Drata's
        default policy templates) and let the user pick which ones to push
        through the lifecycle.

        `statuses`, if given, is passed through as the endpoint's
        `statuses[]` filter -- this is the Policy-level status (ACTIVE /
        ARCHIVED / REPLACED / UNACCEPTABLE / OUTDATED), NOT the
        version-workflow status (DRAFT / NEEDS_APPROVAL / APPROVED /
        PUBLISHED / DISCARDED) -- that one isn't filterable from this
        endpoint at all; see get_policy_status() for how callers determine
        it per-policy. Left unset by default, which returns whatever Drata
        considers its default (active) set of policies.
        """
        records: list = []
        cursor: Optional[str] = None
        for _ in range(max_pages):
            params: dict[str, Any] = {"size": 100}
            if statuses:
                params["statuses"] = statuses
            if cursor:
                params["cursor"] = cursor
            resp = self._request("GET", "/public/v2/policies", params=params)
            body = resp.json()
            page_records = body.get("data", []) if isinstance(body, dict) else []
            records.extend(page_records)
            pagination = body.get("pagination", {}) if isinstance(body, dict) else {}
            cursor = pagination.get("cursor")
            if not cursor or not page_records:
                break
        return records

    def get_policy(self, policy_id: str, expand: Optional[str] = None) -> dict:
        params = {"expand": expand} if expand else None
        resp = self._request("GET", f"/public/v2/policies/{policy_id}", params=params)
        return resp.json()

    def get_current_policy_version(self, policy_id: str) -> Optional[dict]:
        """
        Returns the raw PolicyVersion record this app is driving through the
        lifecycle, via the documented endpoint:
            GET /public/v2/policies/{policyId}/policy-versions?sort=createdAt&sortDir=DESC&size=1

        WHY THIS EXISTS, INSTEAD OF READING GET /public/v2/policies/{id}:
        per Drata's published v2 reference, the single-policy response's
        fields are id, name, description, disclaimer, scope, notifyGroups,
        status, createdAt, currentVersionId, version, subVersion,
        renewalDate, publishedAt, approvedAt, owner, groups, controls,
        weekTimeFrameSlas, gracePeriodSlas, p3MatrixSlas -- note
        `currentVersionId` (just an id), and no `latestVersion` /
        `currentVersion` object at all. Two earlier passes at this app
        guessed there'd be an embedded version object there and both
        produced the same "last seen: ACTIVE" symptom, because they were
        always reading the top-level Policy.status (an active/archived
        flag) with nothing to correctly fall back to.

        WHY NOT `?current=true` (a THIRD earlier guess): the endpoint's
        `current` filter is documented as "Filter to only current Policy
        Versions", which reads like "the latest version" -- but confirmed
        against a live tenant, it returned `{"data": [], ...}` (a clean
        200, zero records) for a policy that had just been successfully
        submitted for approval and definitely had exactly one PolicyVersion
        in NEEDS_APPROVAL. The only explanation that fits: `current` means
        something narrower than "latest" -- almost certainly "is this the
        version backing the policy's live/published content", which stays
        false until a version actually reaches PUBLISHED. That makes it the
        wrong filter for anything still mid-approval.

        So instead of filtering by `current`, this simply asks for the most
        recently created version, sorted newest-first, and takes the first
        row (`sort=createdAt&sortDir=DESC&size=1`) -- documented query
        params on this same endpoint. This app never calls the "create a
        new policy version" endpoint for a policy it's actively driving
        through SubmitForApproval/OverrideApprove/Publish, so a policy in
        that lifecycle always has exactly one PolicyVersion being polled;
        "most recent" and "the one being driven" are the same record here,
        sidestepping the `current` flag's narrower meaning entirely. Falls
        back to an older unhyphenated `versions` path on a 404 only as a
        last resort, in case a tenant is still on a prior naming.

        Every attempt (success or failure, on either path) is recorded to
        `self.last_version_lookup_debug` -- a list of small dicts with the
        path, status code, and either the error or a summary of what came
        back (response shape, top-level keys, record count). This exists
        so that if this method's assumptions are *still* wrong for some
        tenant, the app can show the real HTTP response instead of the
        caller having to guess again -- see the field's docstring on the
        DrataClient dataclass.
        """
        debug: list = []
        self.last_version_lookup_debug = debug
        params = {"sort": "createdAt", "sortDir": "DESC", "size": 1}
        for path in (
            f"/public/v2/policies/{policy_id}/policy-versions",
            f"/public/v2/policies/{policy_id}/versions",
        ):
            try:
                resp = self._request("GET", path, params=params)
            except DrataAPIError as e:
                debug.append(
                    {
                        "path": path,
                        "status_code": e.status_code,
                        "error": str(e),
                        "payload": e.payload,
                    }
                )
                if e.status_code == 404:
                    continue
                raise  # a 401/403/etc. is a real problem, not "try the fallback path"
            body = resp.json()
            records = body.get("data", body) if isinstance(body, dict) else body
            debug.append(
                {
                    "path": path,
                    "status_code": resp.status_code,
                    "response_shape": type(body).__name__,
                    "response_keys": (
                        list(body.keys()) if isinstance(body, dict) else None
                    ),
                    "record_count": (
                        len(records)
                        if isinstance(records, list)
                        else (1 if records else 0)
                    ),
                    "first_record_keys": (
                        list(records[0].keys())
                        if isinstance(records, list) and records and isinstance(records[0], dict)
                        else (
                            list(records.keys())
                            if isinstance(records, dict)
                            else None
                        )
                    ),
                }
            )
            if not records:
                return None
            record = records[0] if isinstance(records, list) else records
            return record
        return None

    def get_policy_status(self, policy_id: str) -> Optional[str]:
        """
        Returns the *version* workflow status (DRAFT / NEEDS_APPROVAL /
        APPROVED / PUBLISHED / DISCARDED) used by the lifecycle actions and
        by wait_for_status -- NOT the Policy entity's own top-level `status`
        field, which is a separate active/archived flag (e.g. "ACTIVE") and
        will never equal any of the version-workflow values.

        Reads the most-recently-created PolicyVersion via
        get_current_policy_version() (see that method's docstring for why
        it sorts by createdAt rather than filtering `current=true`) and
        pulls its `policyVersionStatus` field -- confirmed via Drata's
        published v2 reference to be the actual field name on a PolicyVersion
        record (NOT `status`; that name is easy to assume by analogy with
        the Policy object, but it's wrong here, and reading it first would
        silently swallow a real status via `or` chaining with no error).
        `status` is still checked as a defensive fallback in case a tenant's
        response shape differs, but `policyVersionStatus` is tried first as
        the documented, authoritative field. Falls back to the top-level
        Policy `status` only if no version record comes back at all, so
        callers get *something* rather than an exception -- `wait_for_status`
        will keep polling and this bottoms out to "ACTIVE" only if that
        persists for the whole timeout window, which is a real (not
        phantom) signal worth reporting back.
        """
        version = self.get_current_policy_version(policy_id)
        if version:
            status = version.get("policyVersionStatus") or version.get("status")
            if status:
                return status
        data = self.get_policy(policy_id)
        return data.get("status")

    # ------------------------------------------------------------------
    # Lifecycle actions -- POST /public/v2/policies/{policyId}/actions
    # ------------------------------------------------------------------
    def _perform_action(self, policy_id: str, action: str, **extra) -> dict:
        payload = {"action": action, **extra}
        resp = self._request(
            "POST", f"/public/v2/policies/{policy_id}/actions", json=payload
        )
        return resp.json()

    def submit_for_approval(self, policy_id: str) -> dict:
        """
        DRAFT -> NEEDS_APPROVAL. Returns the raw {success, newStatus,
        message} body Drata's actions endpoint sends back -- callers should
        read `newStatus` from this response as a diagnostic only, not as
        proof the transition already landed (see the module docstring's
        Lifecycle section for why).
        """
        return self._perform_action(policy_id, "SubmitForApproval")

    def override_approve(self, policy_id: str, reason: str) -> dict:
        """
        NEEDS_APPROVAL -> APPROVED. This is the only approval path available
        to an API key (it has no reviewer identity, so plain "Approve" is
        never grantable to it). `reason` is required by Drata and shows up
        in the policy's audit trail. Returns the raw {success, newStatus,
        message} body -- see submit_for_approval()'s docstring on reading
        `newStatus`.
        """
        return self._perform_action(policy_id, "OverrideApprove", overrideReason=reason)

    def publish(self, policy_id: str) -> dict:
        """
        APPROVED -> PUBLISHED. Returns the raw {success, newStatus, message}
        body -- see submit_for_approval()'s docstring on reading `newStatus`.
        """
        return self._perform_action(policy_id, "Publish")

    def discard(self, policy_id: str) -> dict:
        """DRAFT/NEEDS_APPROVAL -> DISCARDED. Not used by the app's default flow."""
        return self._perform_action(policy_id, "Discard")

    def wait_for_status(
        self,
        policy_id: str,
        target_statuses: set,
        timeout: float = 30.0,
        interval: float = 2.0,
        on_poll: Optional[Callable[[str], None]] = None,
    ) -> Optional[str]:
        """
        Polls get_policy_status() until it lands in target_statuses or
        timeout elapses. OverrideApprove and Publish both kick off async
        work in Drata (Temporal -> S3), so the status doesn't flip
        immediately. Returns the last-seen status (which may NOT be in
        target_statuses if it timed out) so the caller can decide what to do.
        """
        elapsed = 0.0
        last_status = None
        while True:
            try:
                last_status = self.get_policy_status(policy_id)
            except DrataAPIError:
                last_status = None
            if on_poll:
                on_poll(last_status)
            if last_status in target_statuses:
                return last_status
            if elapsed >= timeout:
                return last_status
            time.sleep(interval)
            elapsed += interval

    # ------------------------------------------------------------------
    # Lookup helpers (best-effort; used only by the optional UI helpers)
    # ------------------------------------------------------------------
    def find_user_by_email(self, email: str) -> Optional[dict]:
        """
        Resolves any Drata user (not just workforce Personnel) by email via
        GET /public/v2/users/email:{email} -- v2's /users/{id} route treats
        a value prefixed "email:" as a lookup-by-email. This is the right
        call for policy ownership specifically: an org's own admin/service
        accounts are Users but are frequently *not* Personnel records (which
        are workforce/HRIS-tracked), so /public/personnel alone can miss
        exactly the kind of account you'd pick as an owner while testing.

        Falls back to paginating /public/personnel if the direct lookup
        404s, in case a tenant's setup or API version differs. Returns the
        raw record dict (with an "id" field), or None if neither finds it.
        """
        email = email.strip()
        if not email:
            return None

        from urllib.parse import quote

        try:
            resp = self._request(
                "GET", f"/public/v2/users/email:{quote(email, safe='')}"
            )
            return resp.json()
        except DrataAPIError as e:
            if e.status_code not in (400, 404):
                raise  # a 401/403 etc. is a real problem, not "just fall back"

        return self._find_personnel_by_email_paginated(email)

    def _find_personnel_by_email_paginated(
        self, email: str, max_pages: int = 20
    ) -> Optional[dict]:
        """
        Paginates through /public/personnel (v1) looking for a case-insensitive
        email match. Fallback path only -- prefer find_user_by_email, since
        this endpoint only covers workforce/HRIS-tracked Personnel, not every
        Drata user (admin/service accounts in particular are often absent).
        """
        email_lower = email.lower()
        page = 1
        page_size = 50  # /public/personnel rejects limit > 50
        while page <= max_pages:
            resp = self._request(
                "GET", "/public/personnel", params={"page": page, "limit": page_size}
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
            if len(records) < page_size:
                break
        return None

    def _iter_controls(self, max_pages: int = 20):
        """
        Yields every control record, trying /public/v2/controls then
        falling back to /public/controls. Shared by search_controls (fuzzy,
        UI helper) and resolve_control_code (exact, used to auto-resolve a
        typed control code like "DCF-37" into its numeric ID).
        """
        page_size = 50  # Drata's public list endpoints commonly reject limit > 50
        for path in ("/public/v2/controls", "/public/controls"):
            page = 1
            got_any = False
            try:
                while page <= max_pages:
                    resp = self._request(
                        "GET", path, params={"page": page, "limit": page_size}
                    )
                    body = resp.json()
                    records = body.get("data", body) if isinstance(body, dict) else body
                    if not records:
                        break
                    got_any = True
                    for record in records:
                        yield record
                    total_pages = body.get("totalPages") if isinstance(body, dict) else None
                    page += 1
                    if total_pages is not None and page > total_pages:
                        break
                    if len(records) < page_size:
                        break
            except DrataAPIError:
                continue
            if got_any:
                return  # this path worked; don't also try the fallback

    def search_controls(self, query: str, max_pages: int = 10) -> list:
        """
        Best-effort fuzzy control search so the UI can help a user find a
        control's ID from its code/name. Returns a list of raw control
        records for the caller to render as a table.
        """
        query_lower = query.strip().lower()
        matches = []
        for record in self._iter_controls(max_pages=max_pages):
            haystack = " ".join(
                str(record.get(field, ""))
                for field in ("code", "name", "id", "shortName")
            ).lower()
            if query_lower in haystack:
                matches.append(record)
        return matches

    def resolve_control_code(self, code: str, max_pages: int = 20) -> Optional[dict]:
        """
        Finds a single control by exact code (case-insensitive), e.g.
        "DCF-37". Used to auto-resolve a control code typed into the
        Control IDs field into the numeric ID Drata's mapping endpoint
        (PUT /public/v2/policies/{id}, controlIds) actually requires --
        it rejects anything that isn't a plain integer.
        """
        code_lower = code.strip().lower()
        for record in self._iter_controls(max_pages=max_pages):
            if str(record.get("code", "")).strip().lower() == code_lower:
                return record
        return None
