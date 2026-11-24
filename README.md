# Drata Policy Creator

A Streamlit app that creates five policies in your Drata workspace
(`app.drata.com`) via Drata's **Public API**, with:

- a policy owner you assign
- a renewal date you set
- control IDs mapped to the policy
- content pulled from a separate `.txt` file per policy
- everything authenticated with a Drata API key
- each policy, by default, carried all the way to **Published** (or left
  as **Draft**, if you switch the mode in the app)
- an optional **Excel template** to bulk-fill the 5 policy forms' fields
  (name, description, owner, renewal date, control IDs) instead of typing
  them in one at a time
- an optional **replace existing policy** mode, so re-running the app
  against Drata's own default/template policies updates them in place
  instead of creating duplicates

## Quick start

```bash
git clone <this-repo-url>
cd drata-policy-creator
python -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
streamlit run app.py
```

Then, in the app:

1. Paste your Drata API key into the sidebar (or put it in
   `.streamlit/secrets.toml`, see below) and click **Test connection**.
2. Edit `policies/policy_1.txt` through `policies/policy_5.txt` with your
   real policy content (or upload/type content per-policy instead).
3. Choose whether policies should end up **Published** (default) or left
   as **Draft**, set an override-approval reason if publishing, and decide
   whether to **replace an existing policy with the same name** instead of
   creating a new one (see below).
4. Either download the **Excel template**, fill it in, and upload it back
   to pre-fill the forms below — or just fill in each policy's name,
   owner, renewal date, and control IDs by hand.
5. Tick **Preview only** the first time to sanity-check the payloads
   without sending anything, then uncheck it and click **Create all 5
   policies in Drata**.

## Getting a Drata API key

In Drata: **Settings → API Keys → Create API Key**. Grant it:

- **Create Policy** (`create:policy`) — to create policies
- **Policies - Update** — to map control IDs to a newly created policy
- **Policies - Submit for Approval**, **Policies - Override Approve**, and
  **Policies - Publish** — only needed if you're using the default
  Publish mode; skip these if you're leaving policies as Draft

## Deploying via Streamlit Community Cloud (the "from GitHub" part)

1. Push this folder to a new GitHub repo.
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in, and
   click **New app**, pointing it at your repo and `app.py`.
3. In the app's **Settings → Secrets**, add:
   ```toml
   DRATA_API_KEY = "your-key-here"
   ```
   (This pre-fills the sidebar field; you can still override it there.)
4. Deploy. Note that Streamlit Cloud's filesystem is ephemeral/ read-only
   per session for anything not in the repo — if you want to edit policy
   text without a redeploy, use the **Upload a .txt file** or **Type
   content directly** option per policy instead of the local-file-path
   option.

## Finding a policy owner

Type an email and the app resolves it to a user ID via
`GET /public/v2/users/email:{email}`, which covers **any** Drata user —
not just workforce/HRIS-tracked Personnel. That distinction matters: your
org's own admin or service accounts are Users but often aren't Personnel
records, so if you ever see "No Drata user found" for an account you know
exists, double-check the email for typos before assuming it's missing —
and as a fallback you can always switch to **Owner ID (paste directly)**
and paste the ID from the user's profile in the Drata UI.

## Control IDs

The control-mapping field ultimately needs Drata's **internal control ID**
as a plain integer (e.g. `1024`), not the control's code (e.g. `CC6.1` or
`DCF-37`) — Drata's API rejects a code with a 400 (`each value in
controlIds must be an integer number`). You don't have to look codes up
by hand, though: type either the numeric ID or the code directly into a
policy's Control IDs field, comma-separated, and the app resolves any
codes to their IDs automatically (via an exact code match against
`/public/v2/controls`) right before creating that policy, showing you the
`code → id` mapping it found. A code that doesn't match anything is
reported by name and skipped for that policy rather than sent through —
double-check it, or use the standalone **"Look up a Control ID"** helper
near the top of the app (fuzzy search by code or name) to find the right
one.

## Bulk-filling the forms from Excel

Above the 5 policy forms, **Download Excel template** gives you a
`.xlsx` with a `Policies` sheet (one row per policy, numbered 1–5) and an
`Instructions` sheet. The columns are: Policy #, Policy Name, Description,
Owner Email, Owner ID (if not using email), Renewal Date (`YYYY-MM-DD`),
and Control IDs (comma-separated, code or numeric ID — same auto-resolution
as typing them into the app directly).

Policy **content is deliberately not a column** in this template — Drata's
Public API only accepts an uploaded file for content, never inline text, so
content still comes from the existing upload / local file path / typed-text
option per policy below, exactly as before the template existed.

Fill in whichever rows you want, upload the file back with **Upload a
filled-in template**, review the preview table, and click **Apply to policy
forms below** to pre-fill those forms' fields — you can still edit anything
by hand afterward before creating. A few things worth knowing:

- Only Owner Email *or* Owner ID should be filled per row, not both; Email
  takes priority if you fill in both anyway.
- A row is matched to a policy form by its Policy # cell (if it's a valid
  number from 1–5); without that column, rows are matched by position
  instead (first filled-in row → policy 1, and so on).
- Columns are matched by their header text, not position, so reordering
  columns in your copy of the template is fine.
- A renewal date in the past is rejected (Drata requires today or later)
  and left blank, with a warning explaining why.
- A row with neither a Policy Name nor a Description is treated as empty
  and skipped, even if it still has its pre-filled Policy # — so
  downloading the template and immediately re-uploading it without
  changing anything applies nothing.

## Replacing an existing Drata policy instead of creating a duplicate

Turning on **"If a policy with the exact same name already exists in
Drata, update it instead of creating a duplicate"** (in the "After creating
each policy" section) is meant for the case where Drata's workspace already
has a policy — often one of Drata's own built-in default/template
policies — that you want to replace with your own content, rather than end
up with two policies of the same name.

When it's on, the app looks up each policy by its **exact name**
(case-insensitive) in Drata before doing anything else:

- **Exactly one match found:** the app updates that existing policy's
  owner and metadata (description, renewal date), then uploads your
  content as a **new version** of that same policy — it does not touch
  the policy's `name` (Drata's API rejects a name change on its own
  built-in template policies with a 400, and there's no reason to rename
  a policy you just matched by that exact name anyway). That new version
  then goes through the same Submit → Override Approve → Publish flow as
  a normal create, on the same policy ID.
- **No exact match found:** the app just creates a brand-new policy, same
  as if replace mode were off.
- **More than one policy shares that exact name:** the app refuses to
  guess which one you meant, warns you, and creates a new policy instead —
  resolve the duplicate names in Drata first if you want a future run to
  replace one of them.

**A renewal date is required to replace a policy**, even though it's
optional when creating a brand-new one — Drata's "add a new version to an
existing policy" endpoint requires one (today or later), unlike its
"create a brand-new policy" endpoint. If you turn on replace mode for a
policy with no renewal date set, the app stops with a clear error before
sending anything, rather than letting Drata reject it.

## How this maps to Drata's Public API

| App step | Drata endpoint |
|---|---|
| Test connection | `GET /public/v2/policies` |
| Create a policy (starts in Draft) | `POST /public/v2/policies`, **multipart/form-data** — `sourceType: UPLOADED`, `name`, `ownerId`, `description` as form fields, plus a `file` part containing the policy's text (as `.html` or `.txt`, per the format you pick) |
| Find an existing policy by name (replace mode only) | `GET /public/v2/policies?name=...` — a **partial**-match filter, so the app narrows results to an exact, case-insensitive full-name match client-side before treating anything as "the" policy to replace |
| Update an existing policy's owner (replace mode only) | `PUT /public/v2/policies/{policyId}/owner` with `{"userId": ...}` — a separate endpoint from Modify policy below, which doesn't accept an owner field at all |
| Update an existing policy's metadata (replace mode only) | `PUT /public/v2/policies/{policyId}` with `description`/`renewalDate` — same endpoint as Map control IDs below, never sent with `name` (Drata 400s a name change on its own built-in template policies) |
| Replace an existing policy's content (replace mode only) | `POST /public/v2/policies/{policyId}/policy-versions`, **multipart/form-data**, same shape as Create above, but `renewalDate` is **required** here (must be today or later) — creates a new DRAFT PolicyVersion under the existing policy rather than a whole new policy |
| Map control IDs | `PUT /public/v2/policies/{policyId}` with `controlIds: [...]` — **note:** this replaces the full set of control assignments on the policy, it's not additive. Since the app only calls this once, right after creating (or replacing) a policy, that's exactly what you want. |
| Submit for approval | `POST /public/v2/policies/{policyId}/actions` `{"action": "SubmitForApproval"}` — DRAFT → NEEDS_APPROVAL. Response body is `{success, newStatus, message}`, but `newStatus` is diagnostic only — see below for why. |
| Override approve | `POST /public/v2/policies/{policyId}/actions` `{"action": "OverrideApprove", "overrideReason": "..."}` — NEEDS_APPROVAL → APPROVED. Same response shape. |
| Publish | `POST /public/v2/policies/{policyId}/actions` `{"action": "Publish"}` — APPROVED → PUBLISHED. Same response shape. |
| Confirm a policy's workflow status before the next action (always, after every action above) | `GET /public/v2/policies/{policyId}/policy-versions?sort=createdAt&sortDir=DESC&size=1`, field `policyVersionStatus` (falls back to `.../versions` on a 404) — **not** `?current=true` (returns zero records mid-approval — see below) and **not** `GET /public/v2/policies/{policyId}`, which cannot return version status at all |
| Owner lookup by email | `GET /public/v2/users/email:{email}` — falls back to paginating `GET /public/personnel` if that 404s |
| Control search helper | `GET /public/v2/controls` (falls back to `/public/controls`) |

Base URL: `https://public-api.drata.com`. Auth header:
`Authorization: Bearer <API_KEY>`.

### Draft → Published, the API-key way

A newly created policy's initial version comes back in **DRAFT** status.
An API key is a machine-to-machine credential with no reviewer identity
attached to it, so the ordinary `Approve` action (which requires being an
assigned reviewer) is **never** available to a key — the only path a key
can drive is `SubmitForApproval → OverrideApprove → Publish`. That's what
the app does by default for each policy, right after creating it and
mapping any controls.

Drata's own published API reference confirms the `actions` endpoint's
response body already carries `{success, newStatus, message}`, which looks
like a tempting shortcut: check `newStatus` and skip polling if it already
shows the expected value. A version of this app tried exactly that — and
it broke immediately on a live tenant, with `OverrideApprove` failing with
`Action "OverrideApprove" is not available for the current resource state`
right after `SubmitForApproval`'s own response had claimed
`newStatus: "NEEDS_APPROVAL"`. That's proof `newStatus` can report the
*intended* transition before Drata has actually committed it — it's not a
safe "already landed" signal on its own.

So the app now always confirms with a real poll of
`GET /public/v2/policies/{id}/policy-versions?current=true` after every
lifecycle action, regardless of what `newStatus` said (configurable in the
sidebar's **Advanced** section; 30s timeout / 2s interval by default,
enough to ride out a stale first read while the transition catches up).
`newStatus` is still shown in the warning message if a step times out,
purely as an extra diagnostic alongside the last polled value — it just no
longer gets to skip the check that actually matters.

**Why `current=true` was the wrong filter.** This app's polling logic went
through several iterations, and this one was resolved with hard evidence
rather than another guess: the "🔍 Raw policy-versions lookup diagnostics"
block (added specifically to end the guessing) showed a real tenant
returning a clean `200` with `{"data": [], "pagination": {...}}` —
**zero** records — for a policy that had just been successfully submitted
for approval and definitely had one PolicyVersion sitting in
NEEDS_APPROVAL. The only explanation that fits: Drata's `current` filter
on this endpoint doesn't mean "the latest version" (which is what its
one-line description, "Filter to only current Policy Versions", implies)
— it means something narrower, almost certainly "the version backing the
policy's live/published content," which stays false the entire time a
policy is mid-approval and only becomes true once a version reaches
PUBLISHED.

So `get_current_policy_version()` no longer filters by `current` at all.
It instead asks for the single most-recently-created version
(`sort=createdAt&sortDir=DESC&size=1`, both documented params on the same
endpoint) and takes that one. Since this app never calls the "add a new
policy version" endpoint, a policy it created always has exactly one
PolicyVersion — "most recent" and "the only one" are the same record here,
so this sidesteps `current`'s narrower meaning entirely rather than trying
to guess the right value for it.

**If a poll still times out.** The raw-diagnostics mechanism that found
this bug is still in place: every poll records exactly what it got back
(which path responded, the HTTP status, the response's top-level keys, how
many records came back) to `DrataClient.last_version_lookup_debug`, and if
**Submit for Approval**, **Override Approve**, or **Publish** times out,
the app shows it directly in the UI in an expanded "🔍 Raw policy-versions
lookup diagnostics" block right under the warning. Copy that block
verbatim into a bug report — it's what actually diagnosed the
`current=true` issue above, and it'll do the same for whatever's next.

Switch to **Leave as Draft** in the app if you don't want any of this —
policies then stop right after creation (and control mapping), exactly
as the first version of this app did.

**Two status fields, on two different endpoints, with two different field
names — easy to mix up, and this app got it wrong twice before getting it
right.** A `Policy` has a top-level `status` (e.g. `"ACTIVE"` — whether the
*policy entity* is active/archived) that is completely separate from a
`PolicyVersion`'s status — the workflow state (`DRAFT` / `NEEDS_APPROVAL` /
`APPROVED` / `PUBLISHED` / `DISCARDED`) the lifecycle actions and this
whole polling flow actually care about.

The first trap: `GET /public/v2/policies/{id}` — the natural-looking
endpoint to poll — can **only** ever return the top-level one. Per Drata's
published v2 reference, its response fields are `id`, `name`,
`description`, `disclaimer`, `scope`, `notifyGroups`, `status`,
`createdAt`, `currentVersionId`, `version`, `subVersion`, `renewalDate`,
`publishedAt`, `approvedAt`, `owner`, `groups`, `controls`,
`weekTimeFrameSlas`, `gracePeriodSlas`, `p3MatrixSlas` — note
`currentVersionId` is just an id, and there is no embedded `latestVersion`
/ `currentVersion` object at all (that only appears once, in the `POST`
create response, at creation time). Two earlier passes at this app both
guessed there'd be a version object here anyway (first reading the
top-level field outright, then a guessed `latestVersion.status` with a
fallback) and both timed out identically: every poll saw `"ACTIVE"`, never
matched any workflow status, and the app then tried to call the next
lifecycle action too early — producing the `Action "OverrideApprove" is
not available for the current resource state` error above.

The fix was to stop asking the single-policy endpoint for something it
structurally cannot return, and poll the PolicyVersion as its own resource
instead: `GET /public/v2/policies/{id}/policy-versions?current=true`. But
that surfaced a second trap — the field actually holding the workflow
status on that response is named **`policyVersionStatus`**, not `status`
(easy to assume by analogy with the `Policy` object, and wrong: a version
record has no plain `status` field at all in Drata's documented schema).
`get_policy_status()` now reads `policyVersionStatus` first (with `status`
checked defensively after, in case a tenant's response ever differs), and
only falls back to the top-level `Policy.status` if no version record
comes back at all.

A third trap, found the hard way on a live tenant: Drata's `actions`
endpoint response itself carries `{success, newStatus, message}`, which
looks like it should let the app skip polling whenever `newStatus` already
shows the target. It doesn't — a version of this app that trusted it broke
immediately, because `newStatus` can describe the transition Drata
*intends* to make before it's actually committed and visible to a
subsequent `GET`. The app now always confirms with a real poll after every
lifecycle action; `newStatus` is kept around only for the diagnostic
message if that poll times out.

**Content format matters for publishing.** Internal notes on the Publish
action indicate a policy version needs rendered HTML content to publish
successfully. So the app defaults each policy's uploaded-file type to
**HTML** and auto-wraps your plain-text file into simple `<p>`/`<br>`
HTML before uploading it. If you switch a policy to PLAINTEXT and it
fails at the Publish step specifically (not Create), that's the first
thing to try changing back.

### A note on accuracy

Drata's Public API has no "paste raw content" mode for creating a policy
— **only file upload.** An earlier version of this app assumed otherwise
(based on an internal design doc that proposed a `sourceType: BUILDER`
JSON mode) and got a `400` from a real tenant: `content`/`contentFormat`
aren't real fields, `sourceType` only accepts `UPLOADED` or `EXTERNAL`,
and `description` turned out to be required, not optional. `create_policy()`
now sends `multipart/form-data` with `sourceType: UPLOADED` and your
policy text attached as a file, mirroring the field name (`file`) used by
Drata's sibling "add a policy version" endpoint, which their own
engineering notes describe as sharing the exact same upload handling as
this one.

Public APIs do evolve, and this shape was corrected against one real
error, not exhaustively verified end-to-end — if you hit another `400` on
a field name or an unexpected `404`/`403`, double check the live schema
at [developers.drata.com/openapi/reference/v2](https://developers.drata.com/openapi/reference/v2/)
and adjust `drata_client.py` accordingly. The app surfaces the raw error
body from Drata on any failure to make that easy to diagnose — if you hit
one, pasting it back is the fastest way to get the next fix right.

## Files

```
app.py                  Streamlit UI and the create-all-5 workflow
drata_client.py         Thin wrapper around the Drata Public API calls
policies/policy_*.txt   Sample content for each policy — edit these
requirements.txt        Python dependencies
.streamlit/secrets.toml.example   Template for storing your API key locally
```
