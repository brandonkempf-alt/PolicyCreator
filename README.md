# Drata Policy Creator

A Streamlit app that creates five policies in your Drata workspace
(`app.drata.com`) via Drata's **Public API**, each left in **Draft**
status, with:

- a policy owner you assign
- a renewal date you set
- control IDs mapped to the policy
- content pulled from a separate `.txt` file per policy
- everything authenticated with a Drata API key

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
3. Fill in each policy's name, owner, renewal date, and control IDs.
4. Tick **Preview only** the first time to sanity-check the payloads
   without sending anything, then uncheck it and click **Create all 5
   policies in Drata**.

## Getting a Drata API key

In Drata: **Settings → API Keys → Create API Key**. Grant it, at minimum:

- **Create Policy** (`create:policy`) — to create policies
- **Policies - Update** — to map control IDs to a newly created policy

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

## Finding Control IDs

The control-mapping field expects Drata's **internal control ID**, not
just the control's code (e.g. `CC6.1`). Use the built-in **"Look up a
Control ID"** helper in the app (searches by code or name), or find it
from the Drata UI by opening the control and reading the ID out of the
URL.

## How this maps to Drata's Public API

| App step | Drata endpoint |
|---|---|
| Test connection | `GET /public/v2/policies` |
| Create a policy (Draft) | `POST /public/v2/policies` — `sourceType: BUILDER`, with `content`/`contentFormat` set from the policy's text file |
| Map control IDs | `PUT /public/v2/policies/{policyId}` with `controlIds: [...]` — **note:** this replaces the full set of control assignments on the policy, it's not additive. Since the app only calls this once, right after creating a brand-new policy, that's exactly what you want. |
| Owner lookup by email | `GET /public/personnel`, matched client-side by email |
| Control search helper | `GET /public/v2/controls` (falls back to `/public/controls`) |

Base URL: `https://public-api.drata.com`. Auth header:
`Authorization: Bearer <API_KEY>`.

A newly created policy's initial version comes back in **DRAFT** status
by default — the app never calls the policy lifecycle/actions endpoint
(`POST /public/v2/policies/{policyId}/actions`), so nothing gets
submitted for approval or published. It just stays a draft, which is
exactly what was asked for.

### A note on accuracy

The exact request/response shape above reflects Drata's own Public API
v2 policy-creation design as documented internally. Public APIs do
evolve, though — if you hit a `400` on a field name or an unexpected
`404`/`403`, double check the live schema at
[developers.drata.com/openapi/reference/v2](https://developers.drata.com/openapi/reference/v2/)
and adjust `drata_client.py` accordingly. The app surfaces the raw error
body from Drata on any failure to make that easy to diagnose.

## Files

```
app.py                  Streamlit UI and the create-all-5 workflow
drata_client.py         Thin wrapper around the Drata Public API calls
policies/policy_*.txt   Sample content for each policy — edit these
requirements.txt        Python dependencies
.streamlit/secrets.toml.example   Template for storing your API key locally
```
