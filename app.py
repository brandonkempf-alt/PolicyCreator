"""
Drata Policy Creator
=====================
A small Streamlit app that creates five policies in Drata (app.drata.com)
via Drata's Public API v2, with:
  - a policy owner you specify
  - a renewal date you specify
  - control IDs mapped to the policy
  - the policy content pulled from a separate .txt file per policy
  - each policy optionally carried all the way to Published status
    (Draft -> Submit for Approval -> Override Approve -> Publish), since
    an API key has no reviewer identity and can't use plain "Approve"

Run locally:
    pip install -r requirements.txt
    streamlit run app.py

See README.md for how to push this to GitHub and deploy it on
Streamlit Community Cloud, plus notes on where the API details came from.
"""
from __future__ import annotations

import datetime as dt
import html as html_lib
import io

import streamlit as st

from drata_client import DrataClient, DrataAPIError

NUM_POLICIES = 5
DRAFT_ONLY = "Leave as Draft"
PUBLISH_ALL_THE_WAY = "Publish (Submit → Override Approve → Publish)"

st.set_page_config(page_title="Drata Policy Creator", page_icon="📄", layout="wide")

# ----------------------------------------------------------------------
# Session state scaffolding
# ----------------------------------------------------------------------
if "results" not in st.session_state:
    st.session_state.results = []
if "owner_cache" not in st.session_state:
    st.session_state.owner_cache = {}

st.title("📄 Drata Policy Creator")
st.caption(
    "Creates policies in your Drata workspace (app.drata.com) via Drata's "
    "Public API, with an owner, renewal date, and mapped controls — and, by "
    "default, carries each one through to **Published**."
)

# ----------------------------------------------------------------------
# Sidebar: connection
# ----------------------------------------------------------------------
with st.sidebar:
    st.header("Drata API connection")

    try:
        default_key = st.secrets.get("DRATA_API_KEY", "")
    except Exception:
        # st.secrets raises (rather than behaving like a plain dict) when no
        # secrets.toml exists at all, which is the common case for a first
        # local run -- fall back to an empty default in that case.
        default_key = ""
    api_key = st.text_input(
        "API Key",
        value=default_key,
        type="password",
        help=(
            "Create this in Drata under Settings → API Keys. For create + "
            "map controls + publish, grant it: Create Policy, Policies - "
            "Update, Policies - Submit for Approval, Policies - Override "
            "Approve, and Policies - Publish."
        ),
    )

    with st.expander("Advanced"):
        base_url = st.text_input(
            "API base URL",
            value="https://public-api.drata.com",
            help="Change only if Drata has told you to use a different host.",
        )
        poll_timeout = st.number_input(
            "Publish step timeout (seconds)",
            min_value=5,
            max_value=120,
            value=30,
            step=5,
            help=(
                "OverrideApprove and Publish both kick off async processing "
                "in Drata (S3 upload via Temporal). The app polls the policy "
                "until its status catches up, up to this many seconds."
            ),
        )
        poll_interval = st.number_input(
            "Poll interval (seconds)", min_value=1, max_value=15, value=2, step=1
        )

    connect_col, status_col = st.columns([1, 2])
    with connect_col:
        test_clicked = st.button("Test connection")
    if test_clicked:
        if not api_key:
            st.error("Enter an API key first.")
        else:
            client = DrataClient(api_key=api_key, base_url=base_url)
            try:
                client.test_connection()
                st.success("Connected ✅")
            except DrataAPIError as e:
                st.error(f"Failed ({e.status_code}): {e}")
            except Exception as e:
                st.error(f"Failed: {e}")

    st.divider()
    st.caption(
        "This app talks to Drata's Public API (public-api.drata.com), which "
        "is what powers your app.drata.com workspace — it does not go "
        "through the browser app itself."
    )

# ----------------------------------------------------------------------
# Optional helper: look up a control's ID by code/name
# ----------------------------------------------------------------------
with st.expander("🔎 Look up a Control ID (optional helper)"):
    st.write(
        "Control mapping needs Drata's internal control ID(s), not just the "
        "control code. If you already have the IDs, skip this. Otherwise "
        "search by code or name below."
    )
    lookup_query = st.text_input("Search controls (e.g. 'CC6.1' or 'Access Review')", key="lookup_query")
    if st.button("Search", key="lookup_btn"):
        if not api_key:
            st.error("Enter an API key in the sidebar first.")
        elif not lookup_query.strip():
            st.warning("Type something to search for.")
        else:
            client = DrataClient(api_key=api_key, base_url=base_url)
            try:
                matches = client.search_controls(lookup_query)
                if matches:
                    st.dataframe(matches, use_container_width=True)
                else:
                    st.info("No matches found.")
            except DrataAPIError as e:
                st.error(f"Lookup failed ({e.status_code}): {e}")
            except Exception as e:
                st.error(f"Lookup failed: {e}")

st.divider()

# ----------------------------------------------------------------------
# Publish settings (applies to all 5 policies)
# ----------------------------------------------------------------------
st.subheader("After creating each policy")

pub_col, reason_col = st.columns([1, 1.4])
with pub_col:
    lifecycle_mode = st.radio(
        "Target status",
        [PUBLISH_ALL_THE_WAY, DRAFT_ONLY],
        index=0,
        help=(
            "Publishing runs Submit for Approval → Override Approve → "
            "Publish for each policy, right after it's created (and after "
            "any controls are mapped to it). An API key has no reviewer "
            "identity, so 'Override Approve' is the only way a key can move "
            "a policy past NEEDS_APPROVAL."
        ),
    )
with reason_col:
    override_reason = st.text_input(
        "Override approval reason",
        value="Bulk policy creation via API automation",
        disabled=(lifecycle_mode == DRAFT_ONLY),
        help="Required by Drata for the Override Approve step; recorded in the policy's audit trail.",
    )

if lifecycle_mode == PUBLISH_ALL_THE_WAY:
    st.caption(
        "⚠️ Publishing may require the policy content to be well-formed HTML "
        "rather than plain text. Content format defaults to **HTML** below "
        "and plain text you provide is auto-wrapped into simple HTML — if a "
        "policy still fails at the Publish step, that's the first thing to check."
    )

st.divider()

# ----------------------------------------------------------------------
# Policy forms
# ----------------------------------------------------------------------
st.subheader("Define your 5 policies")

policy_inputs = []

for i in range(1, NUM_POLICIES + 1):
    with st.expander(f"Policy {i}", expanded=(i == 1)):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input(f"Policy name #{i}", key=f"name_{i}")
            description = st.text_area(
                f"Description #{i} (optional)", key=f"desc_{i}", height=80
            )
            owner_mode = st.radio(
                f"Policy owner #{i}",
                ["Owner email (looked up)", "Owner ID (paste directly)"],
                key=f"owner_mode_{i}",
                horizontal=True,
            )
            if owner_mode.startswith("Owner email"):
                owner_value = st.text_input(f"Owner email #{i}", key=f"owner_email_{i}")
            else:
                owner_value = st.text_input(f"Owner ID #{i}", key=f"owner_id_{i}")

            renewal_date = st.date_input(
                f"Renewal date #{i} (optional)",
                key=f"renewal_{i}",
                value=None,
                min_value=dt.date.today(),
            )

        with c2:
            content_mode = st.radio(
                f"Policy content source #{i}",
                ["Upload a .txt file", "Read local file path", "Type content directly"],
                key=f"content_mode_{i}",
                horizontal=False,
            )
            uploaded_file = None
            local_path = ""
            direct_text = ""
            if content_mode == "Upload a .txt file":
                uploaded_file = st.file_uploader(
                    f"Policy #{i} content (.txt)", type=["txt"], key=f"upload_{i}"
                )
            elif content_mode == "Read local file path":
                local_path = st.text_input(
                    f"Path to text file #{i}",
                    value=f"policies/policy_{i}.txt",
                    key=f"path_{i}",
                    help="Path is resolved relative to where you run 'streamlit run app.py'.",
                )
            else:
                direct_text = st.text_area(f"Policy #{i} content", key=f"direct_{i}", height=120)

            content_format = st.selectbox(
                f"Content format #{i}",
                ["HTML", "PLAINTEXT"],
                key=f"format_{i}",
                help=(
                    "HTML is recommended, especially if publishing: your text "
                    "is auto-wrapped into simple HTML paragraphs before being "
                    "sent. PLAINTEXT sends the raw text as-is."
                ),
            )
            control_ids_raw = st.text_input(
                f"Control IDs to map #{i} (comma-separated, optional)",
                key=f"controls_{i}",
                help="Drata's internal control IDs, e.g. 1024, 1031 — use the lookup helper above if unsure.",
            )

        policy_inputs.append(
            {
                "index": i,
                "name": name,
                "description": description,
                "owner_mode": owner_mode,
                "owner_value": owner_value,
                "renewal_date": renewal_date,
                "content_mode": content_mode,
                "uploaded_file": uploaded_file,
                "local_path": local_path,
                "direct_text": direct_text,
                "content_format": content_format,
                "control_ids_raw": control_ids_raw,
            }
        )

st.divider()

preview_only = st.checkbox(
    "Preview only (build the request payloads but don't send them)", value=False
)
create_clicked = st.button("🚀 Create all 5 policies in Drata", type="primary")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def resolve_content(policy: dict) -> tuple[str | None, str | None]:
    """Returns (content, error)."""
    if policy["content_mode"] == "Upload a .txt file":
        f = policy["uploaded_file"]
        if f is None:
            return None, "No file uploaded."
        try:
            return io.TextIOWrapper(f, encoding="utf-8").read(), None
        except Exception as e:
            return None, f"Could not read uploaded file: {e}"
    elif policy["content_mode"] == "Read local file path":
        path = policy["local_path"].strip()
        if not path:
            return None, "No file path given."
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read(), None
        except FileNotFoundError:
            return None, f"File not found: {path}"
        except Exception as e:
            return None, f"Could not read file: {e}"
    else:
        text = policy["direct_text"].strip()
        if not text:
            return None, "No content typed."
        return text, None


def resolve_owner_id(client: DrataClient, policy: dict) -> tuple[str | None, str | None]:
    """Returns (owner_id, error)."""
    value = (policy["owner_value"] or "").strip()
    if not value:
        return None, "No owner specified."
    if policy["owner_mode"].startswith("Owner ID"):
        return value, None

    cache = st.session_state.owner_cache
    key = value.lower()
    if key in cache:
        return cache[key], None
    try:
        record = client.find_personnel_by_email(value)
    except DrataAPIError as e:
        return None, f"Owner lookup failed ({e.status_code}): {e}"
    except Exception as e:
        return None, f"Owner lookup failed: {e}"
    if not record:
        return None, f"No personnel record found for '{value}'."
    owner_id = record.get("id") or record.get("userId")
    if not owner_id:
        return None, f"Found a personnel record for '{value}' but it had no id field."
    cache[key] = owner_id
    return owner_id, None


def text_to_simple_html(text: str) -> str:
    """
    Converts plain text (from a .txt file, upload, or typed box) into
    minimal, well-formed HTML: each blank-line-separated block becomes a
    <p>, single line breaks become <br>. Used when content_format == HTML,
    since our inputs are always plain text regardless of the chosen format.
    """
    blocks = [b.strip() for b in text.strip().split("\n\n") if b.strip()]
    paragraphs = []
    for block in blocks:
        escaped = html_lib.escape(block).replace("\n", "<br>\n")
        paragraphs.append(f"<p>{escaped}</p>")
    return "\n".join(paragraphs) if paragraphs else "<p></p>"


def parse_control_ids(raw: str) -> list:
    ids = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if part.isdigit():
            ids.append(int(part))
        else:
            ids.append(part)
    return ids


# ----------------------------------------------------------------------
# Create flow
# ----------------------------------------------------------------------
if create_clicked:
    if not api_key:
        st.error("Enter an API key in the sidebar first.")
    else:
        client = DrataClient(api_key=api_key, base_url=base_url)
        results = []

        for policy in policy_inputs:
            i = policy["index"]
            row = {"Policy #": i, "Name": policy["name"] or "(missing name)"}

            with st.status(f"Policy {i}: {policy['name'] or '(unnamed)'}", expanded=True) as status:
                # Validate name
                if not policy["name"].strip():
                    st.error("Missing policy name — skipped.")
                    row.update({"Result": "❌ skipped", "Detail": "Missing policy name"})
                    status.update(label=f"Policy {i}: skipped (no name)", state="error")
                    results.append(row)
                    continue

                # Resolve content
                raw_content, content_err = resolve_content(policy)
                if content_err:
                    st.error(f"Content error: {content_err}")
                    row.update({"Result": "❌ skipped", "Detail": content_err})
                    status.update(label=f"Policy {i}: skipped (content error)", state="error")
                    results.append(row)
                    continue

                content = (
                    text_to_simple_html(raw_content)
                    if policy["content_format"] == "HTML"
                    else raw_content
                )

                # Resolve owner
                owner_id, owner_err = resolve_owner_id(client, policy)
                if owner_err:
                    st.error(f"Owner error: {owner_err}")
                    row.update({"Result": "❌ skipped", "Detail": owner_err})
                    status.update(label=f"Policy {i}: skipped (owner error)", state="error")
                    results.append(row)
                    continue

                control_ids = parse_control_ids(policy["control_ids_raw"])
                renewal_date_str = (
                    policy["renewal_date"].isoformat() if policy["renewal_date"] else None
                )

                payload_preview = {
                    "name": policy["name"],
                    "description": policy["description"] or None,
                    "ownerId": owner_id,
                    "sourceType": "BUILDER",
                    "contentFormat": policy["content_format"],
                    "content": (content[:200] + "…") if len(content) > 200 else content,
                    "renewalDate": renewal_date_str,
                }
                st.json(payload_preview, expanded=False)

                if preview_only:
                    st.info("Preview only — nothing was sent to Drata.")
                    row.update({"Result": "👁️ preview", "Detail": "Not sent (preview mode)"})
                    status.update(label=f"Policy {i}: previewed", state="complete")
                    results.append(row)
                    continue

                # Create the policy
                try:
                    created = client.create_policy(
                        name=policy["name"],
                        owner_id=owner_id,
                        content=content,
                        description=policy["description"] or None,
                        content_format=policy["content_format"],
                        renewal_date=renewal_date_str,
                    )
                except DrataAPIError as e:
                    st.error(f"Create failed ({e.status_code}): {e}")
                    row.update({"Result": "❌ failed", "Detail": f"Create failed: {e}"})
                    status.update(label=f"Policy {i}: create failed", state="error")
                    results.append(row)
                    continue
                except Exception as e:
                    st.error(f"Create failed: {e}")
                    row.update({"Result": "❌ failed", "Detail": f"Create failed: {e}"})
                    status.update(label=f"Policy {i}: create failed", state="error")
                    results.append(row)
                    continue

                policy_id = created.get("id")
                policy_status = created.get("status") or (created.get("latestVersion") or {}).get("status")
                st.success(f"Created policy id={policy_id}, status={policy_status}")
                row.update({"Policy ID": policy_id, "Status": policy_status})

                # Map controls, if any were given
                if control_ids and policy_id is not None:
                    try:
                        client.map_controls_to_policy(policy_id, control_ids)
                        st.success(f"Mapped controls {control_ids} to policy {policy_id}")
                        row["Controls mapped"] = str(control_ids)
                    except DrataAPIError as e:
                        st.warning(
                            f"Policy created, but control mapping failed ({e.status_code}): {e}"
                        )
                        row["Controls mapped"] = f"failed: {e}"
                    except Exception as e:
                        st.warning(f"Policy created, but control mapping failed: {e}")
                        row["Controls mapped"] = f"failed: {e}"
                else:
                    row["Controls mapped"] = "(none requested)"

                # ------------------------------------------------------------------
                # Publish flow: Submit for Approval -> Override Approve -> Publish.
                # Skipped entirely if the user chose to leave policies as Draft.
                # ------------------------------------------------------------------
                if lifecycle_mode == DRAFT_ONLY or policy_id is None:
                    row["Result"] = "✅ created (Draft)"
                    row["Detail"] = f"id={policy_id}, status={policy_status}"
                    status.update(label=f"Policy {i}: done ✅ (Draft)", state="complete")
                    results.append(row)
                    continue

                def _permission_hint(step: str, e: DrataAPIError) -> str:
                    if e.status_code == 403:
                        return f"{step} failed (403 — API key likely missing the matching permission): {e}"
                    return f"{step} failed ({e.status_code}): {e}"

                # Step 1: Submit for Approval (synchronous)
                st.write("Submitting for approval…")
                try:
                    submit_resp = client.submit_for_approval(policy_id)
                    policy_status = submit_resp.get("newStatus", "NEEDS_APPROVAL")
                    st.success(f"Submitted — status is now {policy_status}")
                except DrataAPIError as e:
                    msg = _permission_hint("Submit for Approval", e)
                    st.warning(f"Policy created, but {msg}")
                    row.update(
                        {
                            "Result": "⚠️ created, not published",
                            "Detail": msg,
                            "Status": policy_status,
                        }
                    )
                    status.update(label=f"Policy {i}: created, submit failed", state="error")
                    results.append(row)
                    continue
                except Exception as e:
                    msg = f"Submit for Approval failed: {e}"
                    st.warning(f"Policy created, but {msg}")
                    row.update(
                        {"Result": "⚠️ created, not published", "Detail": msg, "Status": policy_status}
                    )
                    status.update(label=f"Policy {i}: created, submit failed", state="error")
                    results.append(row)
                    continue

                # Step 2: Override Approve (async — poll for APPROVED)
                st.write("Overriding approval…")
                try:
                    client.override_approve(policy_id, override_reason)
                except DrataAPIError as e:
                    msg = _permission_hint("Override Approve", e)
                    st.warning(f"Policy submitted, but {msg}")
                    row.update(
                        {
                            "Result": "⚠️ created, not published",
                            "Detail": msg,
                            "Status": policy_status,
                        }
                    )
                    status.update(label=f"Policy {i}: submitted, override failed", state="error")
                    results.append(row)
                    continue
                except Exception as e:
                    msg = f"Override Approve failed: {e}"
                    st.warning(f"Policy submitted, but {msg}")
                    row.update(
                        {"Result": "⚠️ created, not published", "Detail": msg, "Status": policy_status}
                    )
                    status.update(label=f"Policy {i}: submitted, override failed", state="error")
                    results.append(row)
                    continue

                with st.spinner("Waiting for approval to process…"):
                    policy_status = client.wait_for_status(
                        policy_id,
                        {"APPROVED"},
                        timeout=poll_timeout,
                        interval=poll_interval,
                    )

                if policy_status != "APPROVED":
                    msg = (
                        f"Approval didn't reach APPROVED within {poll_timeout}s "
                        f"(last seen status: {policy_status}). It may still catch up in "
                        f"Drata — check the policy there before retrying Publish."
                    )
                    st.warning(msg)
                    row.update(
                        {"Result": "⚠️ created, not published", "Detail": msg, "Status": policy_status}
                    )
                    status.update(label=f"Policy {i}: approval timed out", state="error")
                    results.append(row)
                    continue

                st.success("Approved ✅")

                # Step 3: Publish (async — poll for PUBLISHED)
                st.write("Publishing…")
                try:
                    client.publish(policy_id)
                except DrataAPIError as e:
                    msg = _permission_hint("Publish", e)
                    st.warning(f"Policy approved, but {msg}")
                    row.update(
                        {"Result": "⚠️ approved, not published", "Detail": msg, "Status": policy_status}
                    )
                    status.update(label=f"Policy {i}: approved, publish failed", state="error")
                    results.append(row)
                    continue
                except Exception as e:
                    msg = f"Publish failed: {e}"
                    st.warning(f"Policy approved, but {msg}")
                    row.update(
                        {"Result": "⚠️ approved, not published", "Detail": msg, "Status": policy_status}
                    )
                    status.update(label=f"Policy {i}: approved, publish failed", state="error")
                    results.append(row)
                    continue

                with st.spinner("Waiting for publish to complete…"):
                    policy_status = client.wait_for_status(
                        policy_id,
                        {"PUBLISHED"},
                        timeout=poll_timeout,
                        interval=poll_interval,
                    )

                if policy_status == "PUBLISHED":
                    st.success("Published ✅")
                    row["Result"] = "✅ published"
                    row["Detail"] = f"id={policy_id}, status={policy_status}"
                    status.update(label=f"Policy {i}: published ✅", state="complete")
                else:
                    msg = (
                        f"Publish was requested but status hadn't reached PUBLISHED "
                        f"within {poll_timeout}s (last seen: {policy_status}); it may "
                        f"still be finishing in Drata."
                    )
                    st.warning(msg)
                    row.update(
                        {"Result": "⚠️ publish pending", "Detail": msg, "Status": policy_status}
                    )
                    status.update(label=f"Policy {i}: publish pending", state="error")

            results.append(row)

        st.session_state.results = results

if st.session_state.results:
    st.divider()
    st.subheader("Results")
    st.dataframe(st.session_state.results, use_container_width=True)
