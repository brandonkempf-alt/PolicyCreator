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
   as **Draft**, and set an override-approval reason if publishing.
4. Fill in each policy's name, owner, renewal date, and control IDs.
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

## Finding Control IDs

The control-mapping field expects Drata's **internal control ID** as a
plain integer (e.g. `1024`) — not the control's code (e.g. `CC6.1`), which
Drata's API rejects with a 400 (`each value in controlIds must be an
integer number`). The app checks this client-side now and skips mapping
(with a clear warning, not a failed policy) rather than sending a control
code through. Use the built-in **"Look up a Control ID"** helper in the
app (searches by code or name) to get the numeric ID, or find it from the
Drata UI by opening the control and reading the ID out of the URL.

## How this maps to Drata's Public API

| App step | Drata endpoint |
|---|---|
| Test connection | `GET /public/v2/policies` |
| Create a policy (starts in Draft) | `POST /public/v2/policies`, **multipart/form-data** — `sourceType: UPLOADED`, `name`, `ownerId`, `description` as form fields, plus a `file` part containing the policy's text (as `.html` or `.txt`, per the format you pick) |
| Map control IDs | `PUT /public/v2/policies/{policyId}` with `controlIds: [...]` — **note:** this replaces the full set of control assignments on the policy, it's not additive. Since the app only calls this once, right after creating a brand-new policy, that's exactly what you want. |
| Submit for approval | `POST /public/v2/policies/{policyId}/actions` `{"action": "SubmitForApproval"}` — DRAFT → NEEDS_APPROVAL |
| Override approve | `POST /public/v2/policies/{policyId}/actions` `{"action": "OverrideApprove", "overrideReason": "..."}` — NEEDS_APPROVAL → APPROVED |
| Publish | `POST /public/v2/policies/{policyId}/actions` `{"action": "Publish"}` — APPROVED → PUBLISHED |
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

All three steps get a status-poll afterward via `GET /public/v2/policies/{id}`
before the app moves on — `OverrideApprove` and `Publish` clearly kick off
asynchronous work in Drata (an S3 upload via Temporal), and in practice
`SubmitForApproval`'s transition can lag too even though Drata's own docs
describe it as synchronous. Skipping that wait is exactly what produces
Drata's `Action "OverrideApprove" is not available for the current resource
state` error — calling it while the policy is still showing DRAFT. Polling
first (configurable in the sidebar's **Advanced** section; 30s timeout / 2s
interval by default) avoids that. If a policy times out waiting for a
status, the app reports that clearly and leaves it wherever it landed
rather than guessing — check it directly in Drata.

Switch to **Leave as Draft** in the app if you don't want any of this —
policies then stop right after creation (and control mapping), exactly
as the first version of this app did.

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
