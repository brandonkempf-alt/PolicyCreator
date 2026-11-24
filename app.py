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
import re

import openpyxl
import streamlit as st
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

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
if "control_code_cache" not in st.session_state:
    st.session_state.control_code_cache = {}
# Pre-seed each policy's renewal-date key to None *before* the widget below
# ever runs. This is what lets the Excel bulk-import further down set
# st.session_state[f"renewal_{i}"] to a real date and have the date_input
# widget just pick it up -- if that widget also passed an explicit
# `value=None` (it used to), Streamlit warns/conflicts whenever session_state
# already holds a value for that key by the time the widget is created.
# Setting it here, once, and never passing `value=` on the widget itself
# avoids that entirely, for both the plain-typing path and the Excel path.
for _i in range(1, NUM_POLICIES + 1):
    st.session_state.setdefault(f"renewal_{_i}", None)

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
        key="api_key",
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

replace_existing = st.checkbox(
    "If a policy with the exact same name already exists in Drata, update "
    "it instead of creating a duplicate",
    value=False,
    help=(
        "Looks up each policy by exact name (case-insensitive) before "
        "creating anything. If found, this uploads your content as a new "
        "version of that EXISTING policy — updating its owner, "
        "description, and renewal date too — instead of making a "
        "brand-new policy. Useful for replacing Drata's built-in default "
        "policies with your own content without ending up with duplicates. "
        "Requires a Renewal Date to be set for any policy you want "
        "replaced this way (Drata's API requires one when adding a new "
        "version to an existing policy, even though it's optional when "
        "creating a brand-new one). If no exact name match is found, or "
        "more than one exists, this app creates a new policy instead "
        "rather than guessing which one you meant."
    ),
)

st.divider()

# ----------------------------------------------------------------------
# Bulk-fill from Excel (optional) -- everything except policy CONTENT,
# which still comes from the existing upload / local-path / typed options
# per policy below, same as always. Deliberately kept out of the
# spreadsheet: Drata's Public API only accepts an uploaded file for
# content, never inline text, so routing content through a cell value
# first would just be a confusing extra hop before it becomes a file anyway.
# ----------------------------------------------------------------------
def build_policy_template_workbook() -> bytes:
    """Builds the downloadable .xlsx template for the 5 policy forms below."""
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Policies"
    headers = [
        "Policy #",
        "Policy Name",
        "Description",
        "Owner Email",
        "Owner ID (if not using email)",
        "Renewal Date (YYYY-MM-DD)",
        "Control IDs (comma-separated, code or numeric ID)",
    ]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for i in range(1, NUM_POLICIES + 1):
        sheet.append([i, "", "", "", "", "", ""])
    for col_idx, width in enumerate([10, 30, 40, 28, 28, 24, 44], start=1):
        sheet.column_dimensions[get_column_letter(col_idx)].width = width
    sheet.freeze_panes = "A2"

    instructions = wb.create_sheet("Instructions")
    instructions.column_dimensions["A"].width = 100
    lines = [
        "How to use this template",
        "",
        "1. Fill in one row per policy on the 'Policies' sheet, in Policy # order (1-5).",
        "2. Policy Name and Description are required for every row you want created.",
        "3. Owner: fill in EITHER 'Owner Email' (looked up in Drata) OR 'Owner ID' "
        "(pasted directly) — not both. Leave both blank to set the owner manually in the app.",
        "4. Renewal Date must be in YYYY-MM-DD format (e.g. 2027-01-15), or left blank — "
        "except it's REQUIRED for any policy you're replacing (see the app's 'replace "
        "existing policy' option).",
        "5. Control IDs: comma-separated, e.g. '1024, 1031' or 'CC6.1, DCF-37' — codes "
        "are resolved to numeric IDs automatically by the app.",
        "6. Policy CONTENT is not set here — upload it, point to a local .txt file, or "
        "type it directly for each policy in the app itself, same as always.",
        "7. Upload this filled-in file back into the app, review the preview, then click "
        "'Apply to policy forms below'.",
    ]
    for row_i, line in enumerate(lines, start=1):
        c = instructions.cell(row=row_i, column=1, value=line)
        if row_i == 1:
            c.font = Font(bold=True, size=13)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def parse_policy_template(file_bytes: bytes) -> tuple[dict, list]:
    """
    Parses an uploaded filled-in template back into
    {policy_index: {name, description, owner_email, owner_id, renewal_date,
    control_ids_raw}}. Returns (rows_by_index, warnings).

    Columns are looked up by HEADER NAME, not position, so a reordered
    template still works. A row is matched to a policy # by its
    'Policy #' cell when that's a valid 1..NUM_POLICIES integer, otherwise
    by its position among non-blank data rows (1st data row -> policy 1,
    etc.) — capped at NUM_POLICIES since this app's form is fixed at 5.
    """
    warnings: list = []
    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as e:
        return {}, [f"Could not read this file as an Excel workbook: {e}"]

    sheet = wb["Policies"] if "Policies" in wb.sheetnames else wb[wb.sheetnames[0]]
    all_rows = list(sheet.iter_rows(values_only=True))
    if not all_rows:
        return {}, ["The sheet is empty."]

    header = [str(h).strip() if h is not None else "" for h in all_rows[0]]
    col_index = {h: idx for idx, h in enumerate(header) if h}

    def get(row, col_name):
        idx = col_index.get(col_name)
        if idx is None or idx >= len(row):
            return None
        val = row[idx]
        if isinstance(val, str):
            val = val.strip()
        return val if val not in (None, "") else None

    rows_by_index: dict = {}
    fallback_position = 0
    for raw_row in all_rows[1:]:
        if raw_row is None or all(v is None or str(v).strip() == "" for v in raw_row):
            continue
        # The downloaded template pre-numbers every row's "Policy #" cell
        # 1..NUM_POLICIES, so a row the user never actually filled in still
        # has ONE non-blank cell (its number) and would otherwise sail past
        # the all-blank check above. Treat a row as "nothing to apply" (and
        # don't count it toward positional fallback numbering) unless it has
        # a Policy Name or a Description -- the two fields this app actually
        # requires before it will create/replace anything.
        if not get(raw_row, "Policy Name") and not get(raw_row, "Description"):
            continue
        fallback_position += 1

        policy_num = get(raw_row, "Policy #")
        try:
            idx = int(policy_num) if policy_num is not None else fallback_position
        except (TypeError, ValueError):
            idx = fallback_position
        if not (1 <= idx <= NUM_POLICIES):
            warnings.append(
                f"Row with Policy Name '{get(raw_row, 'Policy Name') or '(blank)'}' has an "
                f"out-of-range Policy # ({policy_num!r}) — this app only has "
                f"{NUM_POLICIES} policy forms. Skipped."
            )
            continue

        renewal_raw = get(raw_row, "Renewal Date (YYYY-MM-DD)")
        renewal_date = None
        if renewal_raw is not None:
            if isinstance(renewal_raw, dt.datetime):
                renewal_date = renewal_raw.date()
            elif isinstance(renewal_raw, dt.date):
                renewal_date = renewal_raw
            else:
                try:
                    renewal_date = dt.date.fromisoformat(str(renewal_raw).strip())
                except ValueError:
                    warnings.append(
                        f"Policy #{idx}: couldn't parse renewal date {renewal_raw!r} "
                        "(expected YYYY-MM-DD) — left blank."
                    )
            if renewal_date is not None and renewal_date < dt.date.today():
                warnings.append(
                    f"Policy #{idx}: renewal date {renewal_date.isoformat()} is in the "
                    "past — Drata requires today or later, so it's been left blank. "
                    "Set a future date in the app."
                )
                renewal_date = None

        rows_by_index[idx] = {
            "name": get(raw_row, "Policy Name") or "",
            "description": get(raw_row, "Description") or "",
            "owner_email": get(raw_row, "Owner Email") or "",
            "owner_id": get(raw_row, "Owner ID (if not using email)") or "",
            "renewal_date": renewal_date,
            "control_ids_raw": get(raw_row, "Control IDs (comma-separated, code or numeric ID)") or "",
        }
    return rows_by_index, warnings


st.subheader("Bulk-fill the 5 policy forms from Excel (optional)")
st.caption(
    "Download the template, fill in one row per policy (name, description, "
    "owner, renewal date, control IDs), then upload it here to pre-fill the "
    "forms below instead of typing everything by hand. Policy **content** "
    "still isn't set in the spreadsheet — set it per policy below, same as always."
)
template_col, upload_col = st.columns(2)
with template_col:
    st.download_button(
        "⬇️ Download Excel template",
        data=build_policy_template_workbook(),
        file_name="drata_policy_template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
with upload_col:
    template_upload = st.file_uploader(
        "Upload a filled-in template (.xlsx)", type=["xlsx"], key="template_upload"
    )

if template_upload is not None:
    parsed_rows, parse_warnings = parse_policy_template(template_upload.getvalue())
    for w in parse_warnings:
        st.warning(w)
    if parsed_rows:
        preview_table = []
        for idx in sorted(parsed_rows):
            r = parsed_rows[idx]
            desc = r["description"]
            preview_table.append(
                {
                    "Policy #": idx,
                    "Name": r["name"] or "(blank)",
                    "Description": (desc[:60] + "…") if len(desc) > 60 else (desc or "(blank)"),
                    "Owner": r["owner_email"] or r["owner_id"] or "(blank)",
                    "Renewal Date": r["renewal_date"].isoformat() if r["renewal_date"] else "(blank)",
                    "Control IDs": r["control_ids_raw"] or "(none)",
                }
            )
        st.dataframe(preview_table, use_container_width=True)
        if st.button("Apply to policy forms below", key="apply_template_btn"):
            for idx, r in parsed_rows.items():
                if r["name"]:
                    st.session_state[f"name_{idx}"] = r["name"]
                if r["description"]:
                    st.session_state[f"desc_{idx}"] = r["description"]
                if r["owner_email"]:
                    st.session_state[f"owner_mode_{idx}"] = "Owner email (looked up)"
                    st.session_state[f"owner_email_{idx}"] = r["owner_email"]
                elif r["owner_id"]:
                    st.session_state[f"owner_mode_{idx}"] = "Owner ID (paste directly)"
                    st.session_state[f"owner_id_{idx}"] = str(r["owner_id"])
                if r["renewal_date"]:
                    st.session_state[f"renewal_{idx}"] = r["renewal_date"]
                if r["control_ids_raw"]:
                    st.session_state[f"controls_{idx}"] = r["control_ids_raw"]
            st.success(f"Applied {len(parsed_rows)} row(s) to the forms below.")
            st.rerun()
    else:
        st.info("No usable rows found in that file.")

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
                f"Description #{i}",
                key=f"desc_{i}",
                height=80,
                help="Required by Drata — a short summary of the policy's purpose.",
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
                f"Uploaded file type #{i}",
                ["HTML", "PLAINTEXT"],
                key=f"format_{i}",
                help=(
                    "Policies are created by uploading a file (Drata's Public "
                    "API has no 'paste raw content' mode). HTML is recommended, "
                    "especially if publishing: your text is auto-wrapped into "
                    "simple HTML paragraphs and uploaded as a .html file. "
                    "PLAINTEXT uploads your text as-is in a .txt file."
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
        record = client.find_user_by_email(value)
    except DrataAPIError as e:
        return None, f"Owner lookup failed ({e.status_code}): {e}"
    except Exception as e:
        return None, f"Owner lookup failed: {e}"
    if not record:
        return None, (
            f"No Drata user found for '{value}'. Double-check the email, or "
            "switch this policy's owner to 'Owner ID (paste directly)' and "
            "paste the user ID instead (Drata UI → the user's profile → "
            "check the URL, or Settings → Team)."
        )
    owner_id = record.get("id") or record.get("userId")
    if not owner_id:
        return None, f"Found a user record for '{value}' but it had no id field."
    cache[key] = owner_id
    return owner_id, None


def resolve_control_codes(client: DrataClient, tokens: list) -> tuple[list, list, list]:
    """
    Attempts to resolve each non-integer Control IDs token (e.g. "DCF-37")
    to its numeric control ID via an exact code lookup, since that's what
    Drata's mapping endpoint actually requires. Returns
    (resolved_ids, notes, still_unresolved) -- notes are short strings like
    "DCF-37 → 1024" for display; still_unresolved lists tokens no exact
    code match was found for.
    """
    cache = st.session_state.control_code_cache
    resolved_ids = []
    notes = []
    unresolved = []
    for token in tokens:
        key = token.strip().lower()
        if key in cache:
            resolved_ids.append(cache[key])
            notes.append(f"{token} → {cache[key]}")
            continue
        try:
            record = client.resolve_control_code(token)
        except DrataAPIError:
            record = None
        except Exception:
            record = None
        control_id = record.get("id") if record else None
        if control_id is not None:
            cache[key] = control_id
            resolved_ids.append(control_id)
            notes.append(f"{token} → {control_id}")
        else:
            unresolved.append(token)
    return resolved_ids, notes, unresolved


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


def make_safe_filename(name: str) -> str:
    """Turns a policy name into a filesystem/multipart-safe base filename."""
    safe = re.sub(r"[^A-Za-z0-9._ -]+", "", name).strip().replace(" ", "_")
    return safe or "policy"


def parse_control_ids(raw: str) -> tuple[list, list]:
    """
    Splits a comma-separated Control IDs field. Drata's PUT /policies/{id}
    validation requires every controlId to be a plain integer (a control
    code like "CC6.1" gets a 400: "each value in controlIds must be an
    integer number") -- so anything that doesn't parse as an int is
    reported back as invalid instead of being silently sent and rejected.
    Returns (valid_ids, invalid_tokens).
    """
    ids = []
    invalid = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            invalid.append(part)
    return ids, invalid


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

                # Validate description (Drata requires a non-empty description)
                if not policy["description"].strip():
                    st.error("Missing description — Drata requires one. Skipped.")
                    row.update({"Result": "❌ skipped", "Detail": "Missing description"})
                    status.update(label=f"Policy {i}: skipped (no description)", state="error")
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

                # Drata's create-policy endpoint takes an uploaded file, not
                # inline content -- package our text as one.
                if policy["content_format"] == "HTML":
                    file_bytes = content.encode("utf-8")
                    file_name = f"{make_safe_filename(policy['name'])}.html"
                    file_content_type = "text/html"
                else:
                    file_bytes = content.encode("utf-8")
                    file_name = f"{make_safe_filename(policy['name'])}.txt"
                    file_content_type = "text/plain"

                # Resolve owner
                owner_id, owner_err = resolve_owner_id(client, policy)
                if owner_err:
                    st.error(f"Owner error: {owner_err}")
                    row.update({"Result": "❌ skipped", "Detail": owner_err})
                    status.update(label=f"Policy {i}: skipped (owner error)", state="error")
                    results.append(row)
                    continue

                control_ids, control_code_tokens = parse_control_ids(policy["control_ids_raw"])
                invalid_control_ids = []
                if control_code_tokens:
                    with st.spinner(f"Resolving control code(s) {', '.join(control_code_tokens)}…"):
                        resolved_ids, resolve_notes, invalid_control_ids = resolve_control_codes(
                            client, control_code_tokens
                        )
                    control_ids.extend(resolved_ids)
                    if resolve_notes:
                        st.info("Resolved control code(s): " + "; ".join(resolve_notes))
                    if invalid_control_ids:
                        st.warning(
                            f"Couldn't find a control matching: {', '.join(invalid_control_ids)} "
                            "— double-check the code, or use the lookup helper above to find "
                            "the right one. Skipping these for this policy."
                        )
                renewal_date_str = (
                    policy["renewal_date"].isoformat() if policy["renewal_date"] else None
                )

                # If "replace existing" mode is on, look up a policy with
                # this exact name in Drata BEFORE previewing/sending
                # anything, so the preview below can honestly say whether
                # this run will create a new policy or replace an existing
                # one. find_policy_by_name() only ever returns a single
                # match when exactly one exact (case-insensitive) name
                # match exists -- a partial-match-only or multiple-exact-
                # match result both come back as "no match", so this app
                # never guesses which existing policy was meant.
                existing_policy = None
                if replace_existing:
                    with st.spinner(f"Checking Drata for an existing policy named '{policy['name']}'…"):
                        try:
                            existing_policy, name_matches = client.find_policy_by_name(policy["name"])
                        except DrataAPIError as e:
                            st.warning(
                                f"Lookup for an existing policy named '{policy['name']}' failed "
                                f"({e.status_code}): {e} — creating a new policy instead."
                            )
                            name_matches = []
                        except Exception as e:
                            st.warning(
                                f"Lookup for an existing policy named '{policy['name']}' failed: "
                                f"{e} — creating a new policy instead."
                            )
                            name_matches = []
                        if existing_policy is None and len(name_matches) > 1:
                            st.warning(
                                f"Found {len(name_matches)} existing policies named exactly "
                                f"'{policy['name']}' — too ambiguous to safely replace one "
                                "automatically. Creating a new policy instead; resolve the "
                                "duplicate names in Drata if you want a future run to replace one."
                            )
                is_replace = existing_policy is not None
                verb = "replaced" if is_replace else "created"

                payload_preview = {
                    "name": policy["name"],
                    "description": policy["description"],
                    "ownerId": owner_id,
                    "sourceType": "UPLOADED",
                    "renewalDate": renewal_date_str,
                }
                if is_replace:
                    payload_preview = {
                        "mode": f"REPLACE existing policy id={existing_policy.get('id')}",
                        **payload_preview,
                    }
                st.json(payload_preview, expanded=False)
                preview_snippet = (content[:200] + "…") if len(content) > 200 else content
                st.caption(
                    f"Attached file: **{file_name}** ({file_content_type}, "
                    f"{len(file_bytes)} bytes) — `{preview_snippet}`"
                )
                if is_replace:
                    st.info(
                        f"Found existing policy '{policy['name']}' (id={existing_policy.get('id')}) "
                        "— this run will replace its content instead of creating a new policy."
                    )

                if preview_only:
                    st.info("Preview only — nothing was sent to Drata.")
                    row.update({"Result": "👁️ preview", "Detail": "Not sent (preview mode)"})
                    status.update(label=f"Policy {i}: previewed", state="complete")
                    results.append(row)
                    continue

                if is_replace and not renewal_date_str:
                    msg = (
                        "Replacing an existing policy requires a Renewal Date — Drata's "
                        "'create a new policy version' endpoint requires one (must be "
                        "today or a future date), even though it's optional when creating "
                        "a brand-new policy. Set a renewal date for this policy above and "
                        "try again."
                    )
                    st.error(msg)
                    row.update({"Result": "❌ skipped", "Detail": msg})
                    status.update(label=f"Policy {i}: skipped (renewal date required to replace)", state="error")
                    results.append(row)
                    continue

                if is_replace:
                    # -------- Replace an existing Drata policy's content --------
                    policy_id = existing_policy.get("id")
                    try:
                        client.update_policy_owner(policy_id, owner_id)
                    except DrataAPIError as e:
                        st.warning(
                            f"Found existing policy, but updating its owner failed "
                            f"({e.status_code}): {e}"
                        )
                    except Exception as e:
                        st.warning(f"Found existing policy, but updating its owner failed: {e}")

                    try:
                        client.modify_policy(
                            policy_id,
                            description=policy["description"].strip(),
                            renewalDate=renewal_date_str,
                        )
                    except DrataAPIError as e:
                        st.warning(
                            f"Found existing policy, but updating its metadata failed "
                            f"({e.status_code}): {e}"
                        )
                    except Exception as e:
                        st.warning(f"Found existing policy, but updating its metadata failed: {e}")

                    try:
                        created = client.create_policy_version(
                            policy_id,
                            owner_id=owner_id,
                            renewal_date=renewal_date_str,
                            file_bytes=file_bytes,
                            file_name=file_name,
                            file_content_type=file_content_type,
                            description=policy["description"].strip(),
                        )
                    except DrataAPIError as e:
                        st.error(f"Replacing policy content failed ({e.status_code}): {e}")
                        row.update({"Result": "❌ failed", "Detail": f"Replace content failed: {e}"})
                        status.update(label=f"Policy {i}: replace failed", state="error")
                        results.append(row)
                        continue
                    except Exception as e:
                        st.error(f"Replacing policy content failed: {e}")
                        row.update({"Result": "❌ failed", "Detail": f"Replace content failed: {e}"})
                        status.update(label=f"Policy {i}: replace failed", state="error")
                        results.append(row)
                        continue

                    policy_status = created.get("policyVersionStatus") or created.get("status")
                    st.success(
                        f"Replaced content for existing policy id={policy_id}, "
                        f"new version status={policy_status}"
                    )
                    row.update({"Policy ID": policy_id, "Status": policy_status})
                else:
                    # -------- Create a brand-new policy --------
                    try:
                        created = client.create_policy(
                            name=policy["name"],
                            owner_id=owner_id,
                            description=policy["description"].strip(),
                            file_bytes=file_bytes,
                            file_name=file_name,
                            file_content_type=file_content_type,
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
                    # NOTE: Policy.status (top level) is a separate active/archived
                    # flag, e.g. "ACTIVE" -- NOT the DRAFT/NEEDS_APPROVAL/APPROVED/
                    # PUBLISHED workflow status, which lives on latestVersion.status.
                    # Reading the wrong one here made the lifecycle polling below
                    # time out waiting for a value the top-level field never has.
                    policy_status = (created.get("latestVersion") or {}).get("status") or created.get(
                        "status"
                    )
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
                            f"Policy {verb}, but control mapping failed ({e.status_code}): {e}"
                        )
                        row["Controls mapped"] = f"failed: {e}"
                    except Exception as e:
                        st.warning(f"Policy {verb}, but control mapping failed: {e}")
                        row["Controls mapped"] = f"failed: {e}"
                elif invalid_control_ids and policy_id is not None:
                    row["Controls mapped"] = f"skipped (invalid: {', '.join(invalid_control_ids)})"
                else:
                    row["Controls mapped"] = "(none requested)"

                # ------------------------------------------------------------------
                # Publish flow: Submit for Approval -> Override Approve -> Publish.
                # Skipped entirely if the user chose to leave policies as Draft.
                # ------------------------------------------------------------------
                if lifecycle_mode == DRAFT_ONLY or policy_id is None:
                    row["Result"] = f"✅ {verb} (Draft)"
                    row["Detail"] = f"id={policy_id}, status={policy_status}"
                    status.update(label=f"Policy {i}: done ✅ (Draft)", state="complete")
                    results.append(row)
                    continue

                def _permission_hint(step: str, e: DrataAPIError) -> str:
                    if e.status_code == 403:
                        return f"{step} failed (403 — API key likely missing the matching permission): {e}"
                    return f"{step} failed ({e.status_code}): {e}"

                def _show_version_lookup_debug() -> None:
                    """
                    Shows exactly what the policy-versions polling call(s)
                    actually returned from Drata on the most recent attempt.
                    This app has already shipped two guesses at that
                    endpoint's shape that each matched Drata's own published
                    docs but not a live tenant's real response -- so rather
                    than guess a third time, surface the raw diagnostic
                    right here. If a poll times out, paste this block back
                    verbatim; it's the fastest way to get the next fix right.
                    """
                    debug = getattr(client, "last_version_lookup_debug", None)
                    if not debug:
                        st.caption(
                            "(No policy-versions lookup was attempted — this "
                            "would only happen if the very first status check "
                            "already matched, which isn't the case here.)"
                        )
                        return
                    with st.expander(
                        "🔍 Raw policy-versions lookup diagnostics (last attempt) — "
                        "paste this back if you're reporting this error",
                        expanded=True,
                    ):
                        st.json(debug)

                # Step 1: Submit for Approval. Drata's published v2 reference
                # confirms the actions endpoint's own response body carries
                # {success, newStatus, message} -- but a live tenant showed
                # this isn't a reliable "already committed" signal: an
                # earlier version of this app trusted newStatus and skipped
                # polling whenever it already showed NEEDS_APPROVAL, which
                # immediately produced Drata's "Action ... is not available
                # for the current resource state" 400 on the very next call
                # (Override Approve) -- proof that newStatus can report the
                # *intended* transition before it's actually persisted.
                # So: newStatus is kept only as a diagnostic in the warning
                # messages below; the ACTUAL gate before firing the next
                # lifecycle action is always a real poll of the
                # policy-versions endpoint (which resolves on its first
                # check with no added delay in the normal case where the
                # transition already landed, and retries for the case where
                # it hasn't).
                st.write("Submitting for approval…")
                try:
                    submit_resp = client.submit_for_approval(policy_id)
                    if submit_resp.get("success") is False:
                        raise DrataAPIError(
                            200, submit_resp.get("message", "submit reported failure"), submit_resp
                        )
                except DrataAPIError as e:
                    msg = _permission_hint("Submit for Approval", e)
                    st.warning(f"Policy {verb}, but {msg}")
                    row.update(
                        {
                            "Result": f"⚠️ {verb}, not published",
                            "Detail": msg,
                            "Status": policy_status,
                        }
                    )
                    status.update(label=f"Policy {i}: {verb}, submit failed", state="error")
                    results.append(row)
                    continue
                except Exception as e:
                    msg = f"Submit for Approval failed: {e}"
                    st.warning(f"Policy {verb}, but {msg}")
                    row.update(
                        {"Result": f"⚠️ {verb}, not published", "Detail": msg, "Status": policy_status}
                    )
                    status.update(label=f"Policy {i}: {verb}, submit failed", state="error")
                    results.append(row)
                    continue

                submit_new_status = submit_resp.get("newStatus")
                with st.spinner("Confirming submission…"):
                    policy_status = client.wait_for_status(
                        policy_id,
                        {"NEEDS_APPROVAL", "APPROVED", "PUBLISHED"},
                        timeout=poll_timeout,
                        interval=poll_interval,
                    )

                if policy_status not in ("NEEDS_APPROVAL", "APPROVED", "PUBLISHED"):
                    msg = (
                        f"Submitted, but status hadn't reached NEEDS_APPROVAL within "
                        f"{poll_timeout}s (action response said newStatus="
                        f"{submit_new_status!r}; last polled: {policy_status}) — skipping "
                        f"Override Approve rather than sending an action Drata would reject."
                    )
                    st.warning(msg)
                    _show_version_lookup_debug()
                    row.update(
                        {"Result": f"⚠️ {verb}, not published", "Detail": msg, "Status": policy_status}
                    )
                    status.update(label=f"Policy {i}: submit didn't take effect", state="error")
                    results.append(row)
                    continue

                st.success(f"Submitted — status is now {policy_status}")

                # Step 2: Override Approve. Same pattern as Step 1 -- always
                # confirm with a real poll before moving on (see Step 1's
                # comment for why the action response's own newStatus isn't
                # trusted as a skip-the-check signal). OverrideApprove can
                # also kick off async work in Drata (S3 upload via Temporal),
                # so the poll's retry window matters here too.
                st.write("Overriding approval…")
                try:
                    override_resp = client.override_approve(policy_id, override_reason)
                except DrataAPIError as e:
                    msg = _permission_hint("Override Approve", e)
                    st.warning(f"Policy submitted, but {msg}")
                    row.update(
                        {
                            "Result": f"⚠️ {verb}, not published",
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
                        {"Result": f"⚠️ {verb}, not published", "Detail": msg, "Status": policy_status}
                    )
                    status.update(label=f"Policy {i}: submitted, override failed", state="error")
                    results.append(row)
                    continue

                override_new_status = override_resp.get("newStatus")
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
                        f"(action response said newStatus={override_new_status!r}; last "
                        f"polled: {policy_status}). It may still catch up in Drata — check "
                        f"the policy there before retrying Publish."
                    )
                    st.warning(msg)
                    _show_version_lookup_debug()
                    row.update(
                        {"Result": f"⚠️ {verb}, not published", "Detail": msg, "Status": policy_status}
                    )
                    status.update(label=f"Policy {i}: approval timed out", state="error")
                    results.append(row)
                    continue

                st.success("Approved ✅")

                # Step 3: Publish. Same pattern -- always confirm with a
                # real poll rather than trusting the action response alone.
                st.write("Publishing…")
                try:
                    publish_resp = client.publish(policy_id)
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

                publish_new_status = publish_resp.get("newStatus")
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
                    _show_version_lookup_debug()
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
