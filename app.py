"""
Drata Policy Creator
=====================

A small Streamlit app that creates (or, optionally, replaces the content
of) 5 policies in Drata via Drata's Public API, in one pass:

  - Each policy's content comes from an uploaded file, a path on this
    machine, or text typed directly into the app.
  - Each policy can be given an owner (by email or by numeric Drata user
    ID), a renewal date, and a set of control IDs to map to it.
  - By default, each policy is pushed all the way to Published
    (Submit for Approval -> Override Approve -> Publish) using the same
    API key -- there's a toggle to leave policies as Draft instead.
  - Optionally, instead of creating a brand-new policy, the app can look
    up an existing Drata policy by exact name and replace its content,
    owner, description, and renewal date in place (see the "replace"
    checkbox below the lifecycle settings).

See README.md for setup instructions (API key, deploying to Streamlit
Community Cloud) and a walkthrough of exactly which Drata API calls this
app makes and why.
"""
from __future__ import annotations

import datetime as dt
import html as html_lib
import io
import re

import streamlit as st

from drata_client import DrataClient, DrataAPIError


NUM_POLICIES = 5

DRAFT_ONLY = "Leave as Draft"
PUBLISH_ALL_THE_WAY = "Publish (Submit → Override Approve → Publish)"

st.set_page_config(page_title="Drata Policy Creator", page_icon="📄", layout="wide")

# ----------------------------------------------------------------------
# Session state scaffolding
# ----------------------------------------------------------------------
st.session_state.setdefault("results", [])
st.session_state.setdefault("owner_cache", {})
st.session_state.setdefault("control_code_cache", {})

# Renewal-date widgets are seeded here (once, before they're instantiated
# below) so nothing else needs to pass `value=` on them later -- Streamlit
# warns if a keyed widget gets both a pre-existing session_state entry AND
# an explicit value/index kwarg in the same run.
for _i in range(1, NUM_POLICIES + 1):
    st.session_state.setdefault(f"renewal_{_i}", None)

st.title("📄 Drata Policy Creator")
st.caption(
    "Create (or replace) 5 policies in Drata via the Public API — set an owner, "
    "renewal date, and control mappings for each, then optionally publish them "
    "all the way through Drata's approval workflow."
)

# ----------------------------------------------------------------------
# Sidebar: connection settings
# ----------------------------------------------------------------------
with st.sidebar:
    st.header("Connection")

    default_api_key = ""
    try:
        default_api_key = st.secrets.get("DRATA_API_KEY", "")
    except Exception:
        default_api_key = ""

    api_key = st.text_input(
        "Drata API Key",
        value=default_api_key,
        type="password",
        key="api_key",
        help=(
            "A Drata Public API key (Settings → API Keys in Drata). Stored only "
            "in this browser session — never written to disk by this app. You "
            "can also set DRATA_API_KEY in .streamlit/secrets.toml."
        ),
    )
    base_url = st.text_input(
        "API Base URL",
        value="https://public-api.drata.com",
        key="base_url",
    )

    with st.expander("Advanced"):
        poll_timeout = st.slider(
            "Lifecycle poll timeout (seconds)", min_value=5, max_value=120, value=30
        )
        poll_interval = st.slider(
            "Lifecycle poll interval (seconds)", min_value=1, max_value=15, value=2
        )

    test_clicked = st.button("Test connection")
    if test_clicked:
        if not api_key:
            st.error("Enter an API key first.")
        else:
            try:
                client = DrataClient(api_key=api_key, base_url=base_url)
                client.test_connection()
                st.success("Connected to Drata successfully.")
            except DrataAPIError as e:
                st.error(f"Connection failed ({e.status_code}): {e}")
            except Exception as e:
                st.error(f"Connection failed: {e}")

    with st.expander("🔎 Look up a Control ID"):
        control_query = st.text_input(
            "Search controls by code or name", key="control_query"
        )
        if st.button("Search", key="control_search_btn"):
            if not api_key:
                st.error("Enter an API key first.")
            elif not control_query.strip():
                st.error("Enter a search term first.")
            else:
                try:
                    client = DrataClient(api_key=api_key, base_url=base_url)
                    with st.spinner("Searching controls..."):
                        matches = client.search_controls(control_query)
                    if matches:
                        st.dataframe(
                            [
                                {
                                    "ID": m.get("id"),
                                    "Code": m.get("code"),
                                    "Name": m.get("name"),
                                }
                                for m in matches
                            ],
                            width="stretch",
                        )
                    else:
                        st.info("No matching controls found.")
                except DrataAPIError as e:
                    st.error(f"Search failed ({e.status_code}): {e}")
                except Exception as e:
                    st.error(f"Search failed: {e}")

st.divider()

# ----------------------------------------------------------------------
# Publish settings
# ----------------------------------------------------------------------
st.subheader("Publish settings")

lifecycle_mode = st.radio(
    "After creating (or replacing) each policy:",
    options=[PUBLISH_ALL_THE_WAY, DRAFT_ONLY],
    index=0,
    key="lifecycle_mode",
    help=(
        "Publish drives each policy through Drata's approval workflow using "
        "this API key: Submit for Approval → Override Approve → Publish. "
        "An API key has no reviewer identity, so Override Approve (not a "
        "plain Approve) is the only approval path available to it. Leave "
        "as Draft skips all of that and stops right after creation."
    ),
)
override_reason = st.text_input(
    "Override Approve reason",
    value="Approved via Drata Policy Creator app",
    key="override_reason",
    help="Required by Drata when using Override Approve; shown in the policy's audit trail.",
)
st.caption(
    "Content is uploaded as HTML or plain text (see each policy's **Content format** "
    "setting below) — Drata renders HTML content with formatting; plain text is shown as-is."
)

replace_existing = st.checkbox(
    "If a policy with the exact same name already exists in Drata, update "
    "it instead of creating a duplicate",
    value=False,
    key="replace_existing",
    help=(
        "When checked, the app looks up each policy by its exact name "
        "(case-insensitive) before creating anything. If exactly one "
        "existing policy matches, the app updates that policy's owner, "
        "description, and renewal date, and uploads your content as a new "
        "version of that policy — instead of creating a brand-new policy. "
        "If no policy matches, a new one is created as usual. If more than "
        "one existing policy shares that exact name, the app can't safely "
        "pick one — it falls back to creating a new policy and warns you. "
        "Note: Drata requires a Renewal Date when replacing a policy's "
        "content (it's optional when creating a brand-new policy), so make "
        "sure Renewal Date is set below for any policy you intend to replace."
    ),
)

st.divider()

# ----------------------------------------------------------------------
# Policy forms
# ----------------------------------------------------------------------
st.subheader("Policies")

policy_inputs = []

for i in range(1, NUM_POLICIES + 1):
    with st.expander(f"Policy {i}", expanded=(i == 1)):
        name = st.text_input("Policy name", key=f"name_{i}")
        description = st.text_area(
            "Description", key=f"desc_{i}", help="Required by Drata."
        )

        owner_mode = st.radio(
            "Owner",
            options=["By email", "By Drata user ID"],
            key=f"owner_mode_{i}",
            horizontal=True,
        )
        owner_email = ""
        owner_id_raw = ""
        if owner_mode == "By email":
            owner_email = st.text_input("Owner email", key=f"owner_email_{i}")
        else:
            owner_id_raw = st.text_input("Owner Drata user ID", key=f"owner_id_{i}")

        renewal_date = st.date_input(
            "Renewal date (optional)",
            key=f"renewal_{i}",
            min_value=dt.date.today(),
        )

        content_mode = st.radio(
            "Content source",
            options=["Upload a file", "Local file path", "Type directly"],
            key=f"content_mode_{i}",
            horizontal=True,
        )
        uploaded_file = None
        local_path = ""
        typed_content = ""
        if content_mode == "Upload a file":
            uploaded_file = st.file_uploader(
                "Policy content file", key=f"upload_{i}", type=None
            )
        elif content_mode == "Local file path":
            local_path = st.text_input(
                "Path to a text file on this machine",
                key=f"path_{i}",
                help="e.g. policies/policy_1.txt",
            )
        else:
            typed_content = st.text_area(
                "Policy content", key=f"typed_{i}", height=200
            )

        content_format = st.selectbox(
            "Content format",
            options=["HTML", "Plain text"],
            key=f"format_{i}",
            help="HTML is recommended — Drata renders it with formatting.",
        )

        control_ids_raw = st.text_input(
            "Control IDs to map (comma-separated — numeric IDs or control "
            "codes like DCF-37)",
            key=f"controls_{i}",
        )

        policy_inputs.append(
            {
                "index": i,
                "name": name,
                "description": description,
                "owner_mode": owner_mode,
                "owner_email": owner_email,
                "owner_id_raw": owner_id_raw,
                "renewal_date": renewal_date,
                "content_mode": content_mode,
                "uploaded_file": uploaded_file,
                "local_path": local_path,
                "typed_content": typed_content,
                "content_format": content_format,
                "control_ids_raw": control_ids_raw,
            }
        )

st.divider()

preview_only = st.checkbox(
    "Preview only (don't actually call Drata)", value=False, key="preview_only"
)
create_clicked = st.button("🚀 Create all 5 policies in Drata", type="primary")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def resolve_content(policy: dict) -> tuple[bytes, str, str]:
    """Returns (file_bytes, file_name, content_type) for a policy's content source."""
    mode = policy["content_mode"]
    safe_name = make_safe_filename(policy["name"] or f"policy_{policy['index']}")
    is_html = policy["content_format"] == "HTML"
    ext = "html" if is_html else "txt"
    content_type = "text/html" if is_html else "text/plain"

    if mode == "Upload a file":
        f = policy["uploaded_file"]
        if f is None:
            raise ValueError("No file uploaded.")
        return f.getvalue(), f.name, (f.type or content_type)

    if mode == "Local file path":
        path = policy["local_path"].strip()
        if not path:
            raise ValueError("No file path provided.")
        with open(path, "rb") as fh:
            data = fh.read()
        return data, path.split("/")[-1], content_type

    # Type directly
    text = policy["typed_content"]
    if not text.strip():
        raise ValueError("No content provided.")
    if is_html:
        data = text_to_simple_html(text).encode("utf-8")
    else:
        data = text.encode("utf-8")
    return data, f"{safe_name}.{ext}", content_type


def resolve_owner_id(client: DrataClient, policy: dict):
    """Resolves a policy's owner input (email or ID) to a numeric Drata user ID."""
    if policy["owner_mode"] == "By Drata user ID":
        raw = policy["owner_id_raw"].strip()
        if not raw:
            raise ValueError("No owner ID provided.")
        if not raw.isdigit():
            raise ValueError(f"Owner ID must be numeric, got '{raw}'.")
        return int(raw)

    email = policy["owner_email"].strip()
    if not email:
        raise ValueError("No owner email provided.")

    cache = st.session_state.owner_cache
    if email in cache:
        return cache[email]

    user = client.find_user_by_email(email)
    if not user:
        raise ValueError(f"No Drata user found for '{email}'.")
    owner_id = user.get("id")
    cache[email] = owner_id
    return owner_id


def resolve_control_codes(client: DrataClient, tokens: list) -> tuple[list, list]:
    """
    Resolves a list of raw control tokens (numeric IDs or control codes) to
    numeric IDs. Returns (resolved_ids, unresolved_tokens).
    """
    resolved = []
    unresolved = []
    cache = st.session_state.control_code_cache
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        if token.isdigit():
            resolved.append(int(token))
            continue
        if token in cache:
            if cache[token] is not None:
                resolved.append(cache[token])
            else:
                unresolved.append(token)
            continue
        match = client.resolve_control_code(token)
        if match and match.get("id") is not None:
            cache[token] = match["id"]
            resolved.append(match["id"])
        else:
            cache[token] = None
            unresolved.append(token)
    return resolved, unresolved


def parse_control_ids(raw: str) -> list:
    """Splits a comma-separated Control IDs field into cleaned tokens."""
    return [t.strip() for t in raw.split(",") if t.strip()]


def text_to_simple_html(text: str) -> str:
    """Wraps plain typed text in minimal HTML, preserving paragraph breaks."""
    paragraphs = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    body = "".join(
        f"<p>{html_lib.escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs
    )
    return f"<html><body>{body}</body></html>"


def make_safe_filename(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip())
    return safe.strip("_") or "policy"


# ----------------------------------------------------------------------
# Create / replace flow
# ----------------------------------------------------------------------
if create_clicked:
    if not api_key:
        st.error("Enter a Drata API key in the sidebar first.")
    else:
        client = DrataClient(api_key=api_key, base_url=base_url)
        results = []

        for policy in policy_inputs:
            i = policy["index"]
            row = {"Policy #": i, "Name": policy["name"] or "(unnamed)"}

            with st.status(f"Policy {i}: {policy['name'] or '(unnamed)'}", expanded=True) as status:
                # 1. Validate name
                if not policy["name"].strip():
                    status.update(label=f"Policy {i}: skipped (no name)", state="error")
                    row["Result"] = "⏭️ skipped (no name)"
                    results.append(row)
                    continue

                # 2. Validate description
                if not policy["description"].strip():
                    st.error("Description is required by Drata.")
                    status.update(label=f"Policy {i}: missing description", state="error")
                    row["Result"] = "❌ missing description"
                    results.append(row)
                    continue

                # 3. Resolve content
                try:
                    file_bytes, file_name, file_content_type = resolve_content(policy)
                except Exception as e:
                    st.error(f"Content error: {e}")
                    status.update(label=f"Policy {i}: content error", state="error")
                    row["Result"] = f"❌ content error: {e}"
                    results.append(row)
                    continue

                # 4. Resolve owner
                try:
                    owner_id = resolve_owner_id(client, policy)
                except (DrataAPIError, ValueError) as e:
                    st.error(f"Owner lookup failed: {e}")
                    status.update(label=f"Policy {i}: owner lookup failed", state="error")
                    row["Result"] = f"❌ owner lookup failed: {e}"
                    results.append(row)
                    continue

                # 5. Resolve control IDs
                control_tokens = parse_control_ids(policy["control_ids_raw"])
                control_ids, unresolved = resolve_control_codes(client, control_tokens)
                if unresolved:
                    st.warning(
                        "Ignoring unresolved Control ID(s): "
                        + ", ".join(unresolved)
                        + " — these weren't numeric IDs and didn't match any "
                        "control code in Drata."
                    )

                # 6. Renewal date
                renewal_date_str = None
                if policy["renewal_date"]:
                    renewal_date_str = policy["renewal_date"].strftime("%Y-%m-%d")

                # 7. Look up an existing policy to replace, if requested
                existing_policy = None
                if replace_existing:
                    try:
                        with st.spinner("Checking for an existing policy with this name..."):
                            existing_policy, matches = client.find_policy_by_name(policy["name"])
                        if existing_policy is None and len(matches) > 1:
                            st.warning(
                                f"{len(matches)} existing policies share the exact name "
                                f"'{policy['name']}' — can't tell which to replace, so "
                                "creating a new policy instead."
                            )
                    except DrataAPIError as e:
                        st.warning(f"Lookup for an existing policy failed ({e.status_code}): {e} — creating a new policy instead.")
                    except Exception as e:
                        st.warning(f"Lookup for an existing policy failed: {e} — creating a new policy instead.")

                is_replace = existing_policy is not None
                verb = "replaced" if is_replace else "created"

                # 8. Preview
                payload_preview = {
                    "name": policy["name"],
                    "description": policy["description"],
                    "ownerId": owner_id,
                    "renewalDate": renewal_date_str,
                    "controlIds": control_ids,
                    "file": file_name,
                }
                if is_replace:
                    payload_preview["mode"] = f"REPLACE existing policy id={existing_policy.get('id')}"
                st.json(payload_preview)
                st.caption(f"Attached file: {file_name} ({file_content_type}, {len(file_bytes)} bytes)")
                if is_replace:
                    st.info(f"This will replace the content of existing policy id={existing_policy.get('id')}.")

                if preview_only:
                    status.update(label=f"Policy {i}: previewed (not sent)", state="complete")
                    row["Result"] = "👁️ previewed only"
                    results.append(row)
                    continue

                # 9. Replacing requires a renewal date
                if is_replace and not renewal_date_str:
                    st.error(
                        "Replacing an existing policy requires a Renewal Date "
                        "(Drata requires one when uploading a new version of an "
                        "existing policy). Set a Renewal Date above and try again."
                    )
                    status.update(label=f"Policy {i}: missing renewal date", state="error")
                    row["Result"] = "❌ missing renewal date (required to replace)"
                    results.append(row)
                    continue

                # 10. Create or replace
                policy_id = None
                policy_status = None
                try:
                    if is_replace:
                        policy_id = existing_policy.get("id")
                        try:
                            client.update_policy_owner(policy_id, owner_id)
                        except DrataAPIError as e:
                            st.warning(f"Owner update failed ({e.status_code}): {e} — continuing anyway.")
                        try:
                            client.modify_policy(
                                policy_id,
                                description=policy["description"],
                                renewalDate=renewal_date_str,
                            )
                        except DrataAPIError as e:
                            st.warning(f"Metadata update failed ({e.status_code}): {e} — continuing anyway.")
                        created = client.create_policy_version(
                            policy_id,
                            owner_id=owner_id,
                            renewal_date=renewal_date_str,
                            file_bytes=file_bytes,
                            file_name=file_name,
                            file_content_type=file_content_type,
                            description=policy["description"],
                        )
                        policy_status = created.get("policyVersionStatus") or created.get("status")
                        st.success(f"Replaced content for existing policy id={policy_id}, new version status={policy_status}.")
                    else:
                        created = client.create_policy(
                            name=policy["name"],
                            owner_id=owner_id,
                            description=policy["description"],
                            file_bytes=file_bytes,
                            file_name=file_name,
                            file_content_type=file_content_type,
                            renewal_date=renewal_date_str,
                        )
                        policy_id = created.get("id")
                        # NOTE: Drata's single-policy GET/POST response never embeds
                        # a version-workflow status object (see drata_client.py's
                        # module docstring) -- `latestVersion` is checked defensively
                        # in case a tenant's create response differs, but the
                        # top-level Policy `status` (an ACTIVE/ARCHIVED flag, not a
                        # workflow status) is the realistic fallback here.
                        policy_status = (created.get("latestVersion") or {}).get("status") or created.get("status")
                        st.success(f"Created policy id={policy_id}, status={policy_status}.")
                except DrataAPIError as e:
                    st.error(f"{verb.capitalize()} failed ({e.status_code}): {e}")
                    status.update(label=f"Policy {i}: {verb} failed", state="error")
                    row["Result"] = f"❌ {verb} failed: {e}"
                    results.append(row)
                    continue
                except Exception as e:
                    st.error(f"{verb.capitalize()} failed: {e}")
                    status.update(label=f"Policy {i}: {verb} failed", state="error")
                    row["Result"] = f"❌ {verb} failed: {e}"
                    results.append(row)
                    continue

                row["Policy ID"] = policy_id

                # 11. Map controls (shared between create and replace paths)
                if control_ids and policy_id is not None:
                    try:
                        client.map_controls_to_policy(policy_id, control_ids)
                        st.success(f"Mapped {len(control_ids)} control(s).")
                    except DrataAPIError as e:
                        st.warning(f"Policy {verb}, but control mapping failed ({e.status_code}): {e}")
                    except Exception as e:
                        st.warning(f"Policy {verb}, but control mapping failed: {e}")

                # 12. Lifecycle
                if lifecycle_mode == DRAFT_ONLY or policy_id is None:
                    status.update(label=f"Policy {i}: {verb} (Draft)", state="complete")
                    row["Result"] = f"✅ {verb} (Draft)"
                    results.append(row)
                    continue

                def _permission_hint(err: DrataAPIError) -> str:
                    if err.status_code in (401, 403):
                        return " (check that this API key has permission to manage policies)"
                    return ""

                def _show_version_lookup_debug():
                    debug = getattr(client, "last_version_lookup_debug", None)
                    if debug:
                        with st.expander("Lifecycle polling details", expanded=True):
                            st.json(debug)

                # Step 1: Submit for Approval
                try:
                    submit_result = client.submit_for_approval(policy_id)
                    if submit_result.get("success") is False:
                        msg = submit_result.get("message", "Drata reported failure.")
                        st.warning(f"Policy {verb}, but submit for approval failed: {msg}")
                        status.update(label=f"Policy {i}: {verb}, not published", state="error")
                        row["Result"] = f"⚠️ {verb}, not published"
                        results.append(row)
                        continue
                except DrataAPIError as e:
                    st.warning(f"Policy {verb}, but submit for approval failed ({e.status_code}): {e}{_permission_hint(e)}")
                    status.update(label=f"Policy {i}: {verb}, submit failed", state="error")
                    row["Result"] = f"⚠️ {verb}, submit failed"
                    results.append(row)
                    continue

                reached = client.wait_for_status(
                    policy_id,
                    target_statuses={"NEEDS_APPROVAL"},
                    timeout=poll_timeout,
                    interval=poll_interval,
                )
                if reached != "NEEDS_APPROVAL":
                    st.warning(
                        f"Submitted, but status hadn't reached NEEDS_APPROVAL within "
                        f"{poll_timeout}s (last seen: {reached}) — skipping Override "
                        "Approve rather than sending an action Drata would reject."
                    )
                    _show_version_lookup_debug()
                    status.update(label=f"Policy {i}: {verb}, not published", state="error")
                    row["Result"] = f"⚠️ {verb}, not published (stuck before approval)"
                    results.append(row)
                    continue

                # Step 2: Override Approve
                try:
                    approve_result = client.override_approve(policy_id, override_reason)
                    if approve_result.get("success") is False:
                        msg = approve_result.get("message", "Drata reported failure.")
                        st.warning(f"Policy {verb}, but Override Approve failed: {msg}")
                        status.update(label=f"Policy {i}: {verb}, not published", state="error")
                        row["Result"] = f"⚠️ {verb}, not published"
                        results.append(row)
                        continue
                except DrataAPIError as e:
                    st.warning(f"Policy {verb}, but Override Approve failed ({e.status_code}): {e}{_permission_hint(e)}")
                    status.update(label=f"Policy {i}: {verb}, not published", state="error")
                    row["Result"] = f"⚠️ {verb}, not published"
                    results.append(row)
                    continue

                reached = client.wait_for_status(
                    policy_id,
                    target_statuses={"APPROVED"},
                    timeout=poll_timeout,
                    interval=poll_interval,
                )
                if reached != "APPROVED":
                    st.warning(
                        f"Override-approved, but status hadn't reached APPROVED "
                        f"within {poll_timeout}s (last seen: {reached})."
                    )
                    _show_version_lookup_debug()
                    status.update(label=f"Policy {i}: {verb}, not published", state="error")
                    row["Result"] = f"⚠️ {verb}, not published (stuck before publish)"
                    results.append(row)
                    continue

                # Step 3: Publish
                try:
                    publish_result = client.publish(policy_id)
                    if publish_result.get("success") is False:
                        msg = publish_result.get("message", "Drata reported failure.")
                        st.warning(f"Policy {verb}, but Publish failed: {msg}")
                        status.update(label=f"Policy {i}: {verb}, not published", state="error")
                        row["Result"] = f"⚠️ {verb}, not published"
                        results.append(row)
                        continue
                except DrataAPIError as e:
                    st.warning(f"Policy {verb}, but Publish failed ({e.status_code}): {e}{_permission_hint(e)}")
                    status.update(label=f"Policy {i}: {verb}, not published", state="error")
                    row["Result"] = f"⚠️ {verb}, not published"
                    results.append(row)
                    continue

                reached = client.wait_for_status(
                    policy_id,
                    target_statuses={"PUBLISHED"},
                    timeout=poll_timeout,
                    interval=poll_interval,
                )
                if reached != "PUBLISHED":
                    st.warning(
                        f"Publish sent, but status hadn't reached PUBLISHED within "
                        f"{poll_timeout}s (last seen: {reached})."
                    )
                    _show_version_lookup_debug()
                    status.update(label=f"Policy {i}: publish pending", state="error")
                    row["Result"] = "⚠️ publish pending"
                    results.append(row)
                    continue

                status.update(label=f"Policy {i}: Published ✅", state="complete")
                row["Result"] = "✅ Published"
                results.append(row)

        st.session_state.results = results

if st.session_state.results:
    st.divider()
    st.subheader("Results")
    st.dataframe(st.session_state.results, width="stretch")
