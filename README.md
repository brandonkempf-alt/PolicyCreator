# Drata Policy Creator

A Streamlit app with two tabs for working with policies in Drata using
[Drata's Public API](https://developers.drata.com/openapi/reference/v2/):
**Create / Replace Policies** (create 5 policies in one pass, or replace
existing ones) and **Activate/Publish Templates** (bulk-publish policies
that already exist in your tenant).

Features:

- Connects to Drata via an API key (never hardcoded — entered in the app or
  stored in `.streamlit/secrets.toml`, which is gitignored).
- **Create / Replace Policies** tab:
  - Creates 5 policies, each with its own name, description, owner,
    renewal date, and control mappings.
  - Each policy's content comes from an uploaded file, a local file path,
    or text typed directly into the app.
  - By default, pushes each policy all the way to **Published**
    (Submit for Approval → Override Approve → Publish) using the same API
    key. A toggle lets you leave policies as **Draft** instead.
  - Optional: instead of creating a brand-new policy, look up an existing
    Drata policy by exact name and replace its content, owner,
    description, and renewal date in place — or target one specific
    policy by its exact ID, which is the reliable option when several
    policies share the same name. See
    [Replacing an existing Drata policy](#replacing-an-existing-drata-policy-instead-of-creating-a-duplicate)
    below.
- **Activate/Publish Templates** tab:
  - Lists every policy already in your Drata tenant (including Drata's
    own default policy templates) and lets you bulk-publish any that
    aren't yet Published, without uploading new content. See
    [Activating/publishing existing policy templates](#activatingpublishing-existing-policy-templates)
    below.
- A built-in Control ID lookup so you can find a control's numeric ID (or
  just type its code, like `DCF-37`, and the app will resolve it for you).

## Quick start

1. Clone this repo (or deploy it directly from GitHub — see
   [Deploying via Streamlit Community Cloud](#deploying-via-streamlit-community-cloud)
   below).
2. `pip install -r requirements.txt`
3. Get a Drata Public API key (see below) and either paste it into the
   app's sidebar at runtime, or copy `.streamlit/secrets.toml.example` to
   `.streamlit/secrets.toml` and fill it in.
4. Fill in each of the 5 policy sections: name, description, owner,
   renewal date, content, and control IDs. Sample placeholder content is
   provided in `policies/policy_1.txt` through `policy_5.txt` if you want
   to try the app before writing your real policy text.
5. Click **Test connection** in the sidebar to confirm your API key works.
6. On the **Create / Replace Policies** tab, review the **Publish
   settings** — decide whether policies should be published automatically
   or left as drafts — then click **Create all 5 policies in Drata**.

Tip: check **Preview only** first to see exactly what will be sent to
Drata for each policy before anything is actually created.

Need to publish policies that already exist in Drata (like its default
templates) instead of creating new ones? Use the **Activate/Publish
Templates** tab — see
[Activating/publishing existing policy templates](#activatingpublishing-existing-policy-templates)
below.

## Getting a Drata API key

In Drata, go to **Settings → API Keys** (an Account Owner or Admin may be
required) and generate a new Public API key. Treat it like a password —
anyone with the key can create and publish policies in your tenant.

## Deploying via Streamlit Community Cloud

1. Push this repo to GitHub.
2. In [Streamlit Community Cloud](https://share.streamlit.io), click
   **New app**, point it at your repo and `app.py`.
3. Under **Advanced settings → Secrets**, add:
   ```toml
   DRATA_API_KEY = "your-drata-public-api-key-here"
   ```
   This is optional — the app also accepts the key typed directly into the
   sidebar at runtime — but setting it as a secret pre-fills the field.
4. Deploy. Anyone who opens the app's URL can type in their own key if you
   don't set a shared one via secrets.

**Do not** commit a real `.streamlit/secrets.toml` to your repo — it's
already listed in `.gitignore`. Use Streamlit Community Cloud's own
Secrets manager for deployed instances instead.

## Finding a policy owner

Enter the owner's email address and the app will resolve it to a numeric
Drata user ID automatically (via `GET /public/v2/users/email:{email}`,
falling back to `/public/personnel` if needed). If you already know the
numeric user ID, you can enter that directly instead.

## Control IDs

The **Control IDs to map** field accepts a comma-separated list of either
numeric control IDs or control codes (like `DCF-37`). Codes are resolved
to IDs automatically. Use the **Look up a Control ID** expander in the
sidebar to search by code or name if you're not sure what to enter.

## Replacing an existing Drata policy instead of creating a duplicate

Check **"If a policy with the exact same name already exists in Drata,
update it instead of creating a duplicate"** (just below the Publish
settings) to switch a policy from "always create new" to "replace if
found."

When checked, for each policy the app:

1. Looks up existing Drata policies by that exact name
   (case-insensitive). Drata's search is a partial-match filter, so the
   app narrows the results to exact matches itself before deciding
   anything.
2. If exactly one existing policy matches, it updates that policy's
   owner and description, uploads your content as a new version of that
   policy, and (if set) carries the lifecycle through to Published just
   like a newly created policy.
3. If no policy matches, a brand-new policy is created as usual.
4. If more than one existing policy shares that exact name, the app
   can't safely guess which one you mean — it warns you and falls back
   to creating a new policy instead.

**Renewal Date is required when replacing a policy.** Drata's API
requires a renewal date on every new policy version (unlike creating a
brand-new policy, where it's optional) — if you leave Renewal Date blank
for a policy you're replacing, the app will stop and ask you to set one.

### Replacing an exact policy when several share the same name

Name lookup can't tell two identically-named policies apart — this comes
up often with Drata's own default templates, since enabling more than one
framework can leave several policies with the exact same name (for
example, three separate "Acceptable Use Policy" records). When that
happens, the app can't safely guess which one you meant, so it falls back
to creating a brand-new policy instead and warns you with a message like:

> 3 existing policies share the exact name 'Acceptable Use Policy' — can't
> tell which to replace, so creating a new policy instead.

To replace one of them exactly instead of falling back:

1. Check **replace existing**, then expand the policy you want to set up.
2. Click **"🔍 Find policies named '...'"**. This shows every policy with
   that exact name — ID, status, renewal date, and owner — so you can
   tell them apart.
3. Copy the **ID** of the one you actually mean into **"Exact Policy ID
   to replace"**, just above the Find button.

When that field has a value, the app replaces that exact policy ID
directly and skips the name lookup (and the ambiguity warning) entirely
— this is the reliable way to target one specific policy out of several
that share a name. Leave it blank to keep using name-based lookup as
before.

## Activating/publishing existing policy templates

The **Activate/Publish Templates** tab is for policies that already exist
in your Drata tenant — most commonly Drata's own default policy templates,
which get created automatically (usually sitting in Draft) when you
enable a compliance framework, and need to be pushed through approval
before they count toward your program.

1. Click **"🔄 Load policies from Drata"**. The app fetches every policy in
   the tenant and checks each one's real approval-workflow status (the
   same `policyVersionStatus` lookup the Create/Replace tab uses) — this
   can take a little while if your tenant has many policies, since it's
   one status check per policy.
2. Use **"Filter by name contains"** and **"Hide already-Published"** to
   narrow the list down. Rows already sitting in Draft, Needs Approval, or
   Approved are pre-selected (**Publish?** checked) for you; Published and
   Discarded rows are left unchecked.
3. Adjust the selection as needed, then click **"🚀 Publish selected
   policies"**.

Each selected policy is driven through to Published using the same
Submit for Approval → Override Approve → Publish workflow as the
Create/Replace tab — but **resumed from wherever that policy's current
version already is**, rather than restarted from Draft. A policy already
sitting at Needs Approval only runs Override Approve and Publish; one
already Approved only runs Publish; a policy already Published is left
alone and reported as "no action taken." A policy whose current version
is Discarded can't be auto-published (Drata has no "undiscard" action) and
is skipped with an explanation — upload a new version for it instead,
e.g. via the Create/Replace tab's replace flow.

This tab never uploads new content — it only advances the lifecycle of
whatever content the policy's current version already has.

## How this maps to Drata's Public API

| App action | Endpoint |
|---|---|
| Test connection | `GET /public/v2/policies` |
| Resolve owner by email | `GET /public/v2/users/email:{email}` (falls back to `GET /public/personnel`) |
| Resolve a control code | `GET /public/v2/controls` (falls back to `/public/controls`) |
| Create a policy | `POST /public/v2/policies` (multipart/form-data) |
| Map controls to a policy | `PUT /public/v2/policies/{policyId}` (`controlIds`) |
| Find an existing policy by name | `GET /public/v2/policies?name=...` |
| Look up one exact policy by ID (Exact Policy ID to replace) | `GET /public/v2/policies/{policyId}` |
| List every policy in the tenant (Activate/Publish Templates) | `GET /public/v2/policies` (paginated, no filter) |
| Update an existing policy's owner | `PUT /public/v2/policies/{policyId}/owner` |
| Update an existing policy's description/renewal date | `PUT /public/v2/policies/{policyId}` |
| Replace an existing policy's content | `POST /public/v2/policies/{policyId}/policy-versions` (multipart/form-data) |
| Submit for Approval / Override Approve / Publish | `POST /public/v2/policies/{policyId}/actions` |
| Poll lifecycle status | `GET /public/v2/policies/{policyId}/policy-versions` |

### Draft → Published, the API-key way

An API key has no reviewer identity in Drata, so the only approval action
available to it is **Override Approve** — a plain **Approve** always
requires a human reviewer and is never offered to a key. That makes the
full key-driven path:

```
Draft --SubmitForApproval--> Needs Approval --OverrideApprove--> Approved --Publish--> Published
```

The `actions` endpoint's own response includes a `newStatus` field, but
**don't trust it as proof the transition already landed** — in testing
against a live tenant, a `SubmitForApproval` call reported
`newStatus: "NEEDS_APPROVAL"` in its response body while the policy was
still actually `ACTIVE`/`DRAFT` a moment later, and immediately calling
`OverrideApprove` next produced a real `400`:
`Action "OverrideApprove" is not available for the current resource
state`. `OverrideApprove` and `Publish` both kick off asynchronous work in
Drata, so status changes lag the action call itself. This app always
polls for the *real* status after every lifecycle action and waits (up to
a configurable timeout) before moving to the next step, rather than
trusting `newStatus`.

**Two status fields that are easy to confuse:** a Policy has a top-level
`status` field (e.g. `ACTIVE`/`ARCHIVED` — whether the policy itself is
active, not where it is in the approval workflow), which is completely
separate from a PolicyVersion's `policyVersionStatus` field (`DRAFT` /
`NEEDS_APPROVAL` / `APPROVED` / `PUBLISHED` / `DISCARDED` — the actual
workflow state this app is driving). `GET /public/v2/policies/{id}` only
ever returns the former. Getting the real workflow status requires
`GET /public/v2/policies/{id}/policy-versions`.

That endpoint also has a `current` filter that reads like "give me the
latest version" but isn't — in testing, `?current=true` returned a clean
`200` with **zero records** for a policy that had just been submitted for
approval and definitely had one version in `NEEDS_APPROVAL`. It appears
`current` actually means "is this the version backing the policy's live
published content," which stays false until a version reaches
`PUBLISHED` — exactly the wrong filter for anything still mid-approval.
This app instead asks for the single most-recently-created version
(`sort=createdAt&sortDir=DESC&size=1`, no `current` filter), which is
correct here because the app only ever drives one version per policy
through the lifecycle at a time.

If a lifecycle step times out waiting for a status change, the app shows
a **"Lifecycle polling details"** panel with the raw API responses it
polled — useful for diagnosing a mismatch against a tenant's actual
behavior without guessing blind.

### A note on accuracy

Some of the request shapes above (particularly `create_policy`'s
multipart/form-data upload) were corrected after testing against a real
Drata tenant turned up validation errors that contradicted an earlier
design assumption (a JSON `content`/`contentFormat`/`sourceType: BUILDER`
shape, which — per the resulting 400s — was never actually a valid way to
create a policy). If Drata's API changes and a call in `drata_client.py`
starts failing, check the live reference at
https://developers.drata.com/openapi/reference/v2/ and the raw error body
(surfaced by the app) before assuming the code is right and the tenant is
wrong.

## Files

- `app.py` — the Streamlit UI and orchestration logic.
- `drata_client.py` — a small client wrapping the Drata Public API calls
  this app uses.
- `policies/policy_1.txt` … `policy_5.txt` — sample placeholder policy
  content, safe to use for a test run or replace with your own.
- `requirements.txt` — Python dependencies.
- `.streamlit/secrets.toml.example` — copy to `.streamlit/secrets.toml`
  and fill in your API key (gitignored; never commit the real file).
