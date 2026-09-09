# Baramej Flow — Workflow Automation Engine for Odoo 19

Proprietary, industrial-grade workflow automation engine, built natively on Odoo 19.
Baramej's own alternative to bolting on a third-party tool like n8n or Zapier —
used across the OTA and Omran ITSM tenders, and reusable for future engagements.

## Status: E-Service Ticket Pattern ✅ Complete (Phases 1–3, Phase 1b, and Integrations also complete)

This adds the pattern you described: a citizen logs in, fills out a form, and the
submission becomes a trackable case that staff process — assign, approve/reject/
request-info, with a payment step — all wired together visually on the canvas.

### The core new capability: pause/resume execution
Before this, a workflow run either finished or failed in one call. Now a node can
signal `{'pause': True}` from `execute()`, and the engine stops mid-graph, marks
the execution `waiting`, records exactly which node it's paused at, and snapshots
the live context. `WorkflowEngine.resume(execution, branch=..., context_update=...)`
picks up from there later — in a completely different request, possibly days
later. This is genuinely new engine architecture, not a bolt-on: `run()` and
`resume()` now share one internal `_walk()` loop, and execution log sequence
numbers continue across a pause/resume rather than resetting.

**Tested via 6 dedicated pause/resume scenarios** using the real E-Service nodes
against a real ticket: pause-and-stop, resume-and-continue, log sequence
continuity, context persistence across the pause boundary, resuming a non-waiting
execution raising cleanly, and a dead-end branch after resume completing
successfully rather than erroring.

### New node types
| Node type | Behavior |
|---|---|
| `assign_employee` | Static assignment to a specific user; also updates the linked ticket's Assigned To |
| `human_approval` | **Pauses.** Sets ticket to "in review"; resumes down `approved`/`rejected`/`more_info` branches when staff act |
| `wait_for_resubmission` | **Pauses.** Sets ticket to "waiting on applicant"; resumes when they resubmit via the portal |
| `send_payment_link` | **Pauses.** Generates a link via `payment.link.wizard`; resumes via a polling cron once payment is confirmed |

### baramej.flow.ticket — the E-Service case object
A first-party model (not a Helpdesk inheritance — see the model's docstring for
why), with `mail.thread` + `portal.mixin` for chatter and citizen visibility.
Kanban grouped by status (submitted → in_review → waiting_info/waiting_payment →
approved/rejected/done). Staff get Approve / Reject (with reason, via wizard) /
Request More Info (via wizard) buttons directly on the ticket form — each one
calls the corresponding `action_*` method, which calls `_resume_execution()`.

### Portal: login-gated submission + status tracking
- `baramej.flow.form.require_login` (default **True**) — an unauthenticated visitor
  hitting a login-required form is redirected to `/web/login?redirect=...`
- On submission, a ticket is created and linked to the logged-in user's partner
- `/my/eservices` — list of the citizen's own applications
- `/my/eservices/<id>` — status, submitted data, a Pay Now link when
  `waiting_payment`, the standard portal message thread for staff↔citizen chat, and
  a resubmission form when `waiting_info`
- **Backward compatible**: forms with `require_login=False` (the Phase 2 default)
  skip ticket creation entirely and behave exactly as before — nothing about
  existing non-ticketed forms changed

### Sample workflow: "Get Approval to Register Taxi Plate"
Installed automatically as demo data (`demo/demo_eservice_workflow.xml`) —
directly modeling the example you gave: Form Trigger → Assign to Reviewer → Ministry
Review (human_approval) → on **approved**, Collect Approval Fee (payment link); on
**rejected**, dead end (reason already recorded); on **more info**, Wait For
Applicant → loops back to Ministry Review. See "Try It Yourself" below.

### What's honestly unverified or deferred
- **Payment integration is the least-tested piece.** `payment.link.wizard`'s exact
  field names have shifted across Odoo versions; I could not test against a live
  `payment` module + provider from this environment. The demo's payment node will
  raise a clear error until you've actually configured a provider — that's
  expected, not a bug.
- **Payment confirmation is a polling cron (every 10 min), matched heuristically**
  by partner + recency, not an exact `payment.transaction` id stored on the
  ticket. Hardening this to store the exact transaction reference is a contained,
  well-understood follow-up.
- **No file upload support in the Form Builder yet** — the real Taxi Plate service
  needs document uploads (contract copy, non-conviction certificate, etc.); the
  demo uses text-reference stand-ins and says so explicitly in the field label.
  Needs a new `file` field_type plus `ir.attachment` handling in the form
  controller.
- **Portal QWeb templates are unverified rendering** — same caveat as the canvas:
  I validated the XML is well-formed, not that it renders correctly in a browser.
- **No "add tile to portal home page"** — `/my/eservices` works as a direct URL;
  I deliberately didn't hand-write the `_prepare_home_portal_values`/counters
  wiring that adds a home-page tile, since that mechanism varies enough across
  Odoo versions that guessing at it felt riskier than leaving it out.
- **Static employee assignment only** — no role/team/load-based routing yet.

## Try It Yourself

1. Update the module: `odoo-bin -d your_db -u baramej_flow --stop-after-init` (make
   sure demo data is enabled on this database, or the sample workflow won't load)
2. Go to **Baramej Flow → Web Forms**, open **"Register Taxi Plate — Application,"**
   click **Publish**
3. Also publish the linked workflow: open **Baramej Flow → Workflows →
   "Get Approval to Register Taxi Plate,"** click **Publish**
4. Copy the form's **Public URL**, open it in an incognito window — you should be
   redirected to log in first (create/use a portal user account)
5. Fill in and submit the application — you'll land on `/my/eservices/<id>`
   showing status **"in_review"**
6. As an internal Flow Manager/admin user, go to **Baramej Flow → E-Service
   Tickets**, open the new ticket (kanban column "in_review")
7. Click **Request More Info**, enter a message — refresh the portal page as the
   citizen: status is now **"waiting_info"**, your message is in the chat thread,
   and a resubmission box appears
8. Submit a resubmission — back on the backend ticket, status returns to
   **"in_review"** (looped back through Ministry Review, exactly as wired)
9. Click **Approve** — status moves to **waiting_payment** if you have a payment
   provider configured (or fails clearly if not, which is expected); click
   **Reject** instead on a fresh test ticket to see the dead-end branch and
   rejection reason recorded

## Status: Phase 1b + Integrations — ✅ Complete (Phases 1–3 also complete)

This round combined the visual canvas (Phase 1b) with a large slice of what would
normally be a separate integrations phase, at your request. Sequencing note for
context: everything below was built and validated the same way as prior phases
(Python compiles clean, node registry cross-checked against the model's Selection
field — 18/18 match), **except the canvas JavaScript**, which is the one part of
this whole module I have no way to execute or render from this environment. Report
back exactly what breaks and I'll fix it precisely.

### Visual Canvas (Phase 1b)
A real drag-and-drop workspace, layered on the *exact same* `baramej.flow.node` /
`baramej.flow.edge` data model the structured builder already used — nothing about
the data changed, since `pos_x`/`pos_y` were reserved on every node since Phase 1
specifically for this.

- Open it via the **Open Canvas** button on a workflow's form view
- Drag nodes to reposition (position saves on release, not on every mouse-move)
- Drag from a node's right-edge handle onto another node to connect them
- Click a connection's midpoint × to delete it; click a node's × to delete it
- "+ Add Node" dropdown covers every registered node type, grouped by category
- Node **layout and wiring** happen on the canvas; detailed **config_json editing**
  still happens on the form view's Nodes tab — the canvas didn't try to reproduce
  a full JSON editor inline, since that's more reliably done in a proper text field
- Built as a plain OWL client action (`ir.actions.client`, tag
  `baramej_flow_canvas`) using absolutely-positioned divs + one SVG overlay for
  connection lines — no third-party graph library, so there's nothing extra to
  vet for a security review

### Generic Integration Nodes
Three nodes cover almost everything you asked for without needing eight bespoke
connectors:

| Node type | Purpose |
|---|---|
| `http_request` | Any REST API — Supabase, or literally anything else. Supports none/api_key_header/bearer/basic/google_oauth2 auth. |
| `db_query` | Direct SQL against an external Postgres or Supabase database (NOT Odoo's own DB). SELECT-only by default; `allow_write: true` required to INSERT/UPDATE/DELETE. Parameters are bound via psycopg2, never string-concatenated — deliberate SQL-injection protection. |
| `read_excel` | Reads an uploaded `.xlsx` `ir.attachment` into row dicts keyed by header. |

### Google OAuth2 + Convenience Wrappers
`baramej.flow.credential` gained a `google_oauth2` provider with OAuth2 refresh-
token handling (`engine/nodes/google_oauth_base.py`) — this implements the
standard OAuth2 refresh-token grant (RFC 6749 §6) and auto-renews the short-lived
access token on every use. **You must obtain a refresh token externally first**
(e.g. via Google's OAuth Playground: https://developers.google.com/oauthplayground)
with the scopes your workflows need — this module does not implement the
interactive consent-screen redirect itself.

| Node type | API | 
|---|---|
| `google_sheets_append` | Sheets API v4 `values:append` |
| `google_sheets_read` | Sheets API v4 `values:get` |
| `gmail_send` | Gmail API `users.messages.send` (sends AS that Google account, not through Odoo's mail server) |
| `google_calendar_create_event` | Calendar API `events.insert` |
| `google_drive_list` | Drive API `files.list` (read-only — see deferred items) |

Every wrapper is thin — the same result is always achievable with a plain
`http_request` node pointed at the same URL; these exist purely to save you
hand-writing each API's payload shape.

**26 new automated tests** cover: HTTP auth types (bearer/none, credential lookup,
non-2xx handling), DB query write-blocking and SELECT parsing (mocked psycopg2 —
no real database touched), Excel reading (a real `.xlsx` built and read in-memory
with openpyxl), OAuth2 token refresh/caching/revocation handling, and all four
Google wrapper nodes (mocked HTTP — no real Google API touched).

### What's deferred, and why
- **Google Drive upload** — only `google_drive_list` (read) was built. Upload needs
  multipart/resumable upload handling that adds real complexity; flagged as the
  natural next increment if you actually need it, rather than shipping something
  half-tested.
- **No connection pooling on `db_query`** — each run opens and closes its own
  Postgres connection. Fine at the traffic volumes discussed for OTA/Omran ITSM;
  would need revisiting for very high-frequency triggers.
- **Canvas is layout+wiring only** — no inline config_json editor, no minimap, no
  node grouping, no zoom controls yet. Functional for building and rewiring a
  workflow visually; the more polished n8n-style canvas features from the original
  Phase 1b spec (grouping, minimap, spatial zoom) remain a later pass.
- **OAuth2 flow genuinely untested against live Google endpoints** from this
  environment — no network egress to `oauth2.googleapis.com` here. The refresh-
  token grant implementation follows the OAuth2 spec faithfully, but your first
  real run against an actual Google Cloud OAuth app is the true test, the same way
  the email gateway needed a live mail server to fully confirm.
- **`psycopg2` and `openpyxl`** are now declared in `external_dependencies` — Odoo
  will refuse to install the module with a clear message if either is missing from
  your venv, rather than failing confusingly at runtime. `psycopg2` should already
  be present (it's how Odoo itself talks to its own database); `openpyxl` likely
  needs `pip install openpyxl`.

## Status: Phase 3 — AI Decision Layer ✅ Complete (Phases 1 & 2 also complete)

### What Phase 3 added

**Credential vault goes live.** `baramej.flow.credential` (staged as an inert model
since Phase 1) is now actually read by AI nodes, and got a **Test Connection**
button that calls the real provider (`GET /v1/models` for OpenAI, the equivalent
Gemini endpoint) so a bad key is caught when you save it, not three layers deep in
a failed workflow run.

**Two AI connector nodes**, both following the same contract:

| Node type | Provider | Structured-output mechanism |
|---|---|---|
| `ai_openai` | OpenAI Chat Completions | `response_format: {"type": "json_object"}` |
| `ai_gemini` | Google Gemini | `generationConfig.responseMimeType: "application/json"` |

Both nodes share one base class (`engine/nodes/ai_base.py`) — credential lookup,
`{{dotted.path}}` prompt interpolation (same syntax as every other node, via the
Phase 1 templating helper), and token-usage logging all live there once. Adding a
third provider later is a ~60-line subclass implementing one method
(`_call_provider`), not a re-implementation.

**Config shape (same for both providers):**
```json
{
  "credential_id": 4,
  "model": "gpt-4o-mini",
  "system_prompt": "Classify IT support tickets by category and priority.",
  "user_prompt_template": "Ticket: {{form.description}}",
  "output_key": "ai_result",
  "json_schema": {"category": "string", "priority": "string", "confidence": "number"}
}
```
The node always instructs the provider to return JSON matching `json_schema` — this
is what makes "AI reads the form then decides the path" reliable rather than
free-text guessing a downstream node can't safely parse.

**Confidence-threshold / classification-based routing — reused, not reinvented.**
Rather than adding a separate "AI branching" node type, the existing `condition`
node was extended with dotted-path field lookup (`engine/utils.get_nested`), so:
```json
{"field": "ai_result.confidence", "operator": ">=", "value": 0.8}
```
routes true/false directly off an AI node's output. This was flagged as the plan
back in Phase 1's `condition.py` docstring, and keeping it this way means Phase 3
didn't grow the node count for something the engine could already do — one fewer
node type to document, test, and keep in sync with the Selection field.

**16 new automated tests** cover: nested-path resolution (including the
backward-compatibility guarantee that flat field names still work exactly as
before), AI→Condition routing on both branches, structured-output parsing for both
providers (via mocked HTTP calls — no real API keys touched during testing),
prompt-template interpolation actually reaching the request payload, provider-error
handling, missing-credential and missing-API-key failure messages, and the
Test Connection action's error paths.

### What's still deferred from Phase 3
- **No retry/backoff** on AI API calls yet — a transient network blip fails the node
  outright. That's Phase 5 (Execution Engine Hardening).
- **Encryption at rest** for `api_key` and per-read audit logging remain on the
  Phase 6 governance list, as originally scoped.
- **Synchronous execution** still applies — an AI node adds real latency (typically
  1–5 seconds) to whichever HTTP request triggered the workflow (webhook, WhatsApp,
  form submit). This is the clearest argument yet for prioritizing Phase 5's async
  queue next.
- **json_schema is advisory, not enforced Odoo-side** — it's passed to the provider
  as a hint in both the system prompt and (for OpenAI) as part of JSON mode, but
  this module doesn't validate the returned JSON actually matches the schema shape
  before writing it to context. A provider returning `{"result": "foo"}` when you
  expected `{"category": ..., "confidence": ...}` won't fail the node — it'll just
  make the next Condition node's `get_nested` lookup return `None`. Worth adding
  explicit schema validation in a later pass if this becomes a real issue in
  practice.

## Status: Phase 2 — Trigger Endpoints ✅ Complete (Phase 1 also complete)

### Patch: engine now runs with sudo() authority (fixed post-Phase-2)
Real-world testing surfaced a genuine bug: the four new trigger endpoints arrive as
Odoo's public user (no Flow Manager/Flow User group), and while individual nodes like
Create Record already ran with `sudo()`, the engine's own bookkeeping — creating the
`baramej.flow.execution` and `baramej.flow.execution.log` records — did not. Every
public-triggered run failed immediately with a 403 before a single node executed.
Fixed by having `WorkflowEngine.__init__` sudo the environment once, consistently, at
the source — matching the "workflows act with their own authority" principle already
documented (but only half-applied) in Phase 1. Access to *build/edit/publish*
workflows is unaffected and still correctly gated by the Flow Manager group.

### Patch log (bugs found during real Odoo 19 testing, fixed post-Phase-2)
- **Engine sudo bug**: `WorkflowEngine.__init__` used `env.sudo()`, but Odoo 19's
  refactored ORM removed `Environment.sudo()` (it now only exists on recordsets).
  Fixed with `env(su=True)`, the environment-level equivalent. Every public-triggered
  workflow (webhook/WhatsApp/form) was failing with a 403 before this fix — the node
  executors' own `.sudo()` calls masked the issue during manual "Run Test" runs
  (which go through a logged-in internal user), so it only surfaced once real
  external traffic hit the endpoints.
- **Form controller 404 handling**: `request.not_found()` was being *returned*
  instead of *raised*, which this Odoo 19 build logs as a warning and handles
  incorrectly. Fixed by raising `werkzeug.exceptions.NotFound()` instead, in both
  `render_form` and `submit_form`.

### What Phase 2 added

**Four new live trigger types**, matching the pattern the `trigger_manual` node
already established in Phase 1:

| Trigger | How it works |
|---|---|
| **Generic Webhook** | `POST /flow/webhook/<uuid>` — HMAC-SHA256 signed (`X-Flow-Signature` header), JSON body lands in context under `webhook` |
| **WhatsApp** | `GET /flow/whatsapp/webhook/<uuid>` handles Meta's verification handshake; `POST` receives messages, parsed and run one execution per message, context under `whatsapp` |
| **Incoming Email** | Each workflow gets its own `mail.alias` (via the "Generate Email Trigger Address" button); inbound mail is logged in `baramej.flow.inbound.mail` (full audit trail) and triggers the workflow, context under `email` |
| **Web Form** | New `baramej.flow.form` + `baramej.flow.form.field` models — a no-code field designer (9 field types incl. dropdown, email, date); publishing renders a public, styled HTML form at `/flow/form/<uuid>`; submissions land in context under `form` |

**New node types**: `trigger_webhook`, `trigger_email`, `trigger_whatsapp`, `trigger_form` —
all simple passthroughs (same pattern as `trigger_manual`), so every workflow has a
correctly-labeled starting node on the canvas matching its real trigger.

**Security/quality decisions worth knowing about:**
- The webhook secret is rotatable (`Regenerate` button) — rotation immediately invalidates
  the old secret, on purpose.
- The public form has a hidden honeypot field for basic bot mitigation. This is **not** a
  substitute for real rate limiting — that's still Phase 6.
- The form renderer deliberately does **not** depend on the `website` module — it returns
  hand-built HTML — keeping the module's dependency footprint at just `base` + `mail`.
- Publishing a Web Form is blocked if its linked workflow isn't Published yet, so a public
  URL can never point at a draft workflow.
- 13 new automated tests cover: node-registry/selection-field consistency, HMAC signature
  verification (valid/tampered/missing), WhatsApp payload parsing (including Meta's
  delivery-receipt callbacks, which must NOT be mistaken for messages), form field name
  validation, and a full form-submission-to-created-record integration test.

### What's still simulated / deferred from Phase 2
- **Email**: the `message_new` hook is real and matches the pattern Odoo's own Helpdesk/
  Project apps use, but it's only been exercised via the test suite's mocked context, not
  against a live inbound SMTP setup — worth a real end-to-end send/receive test on your
  actual Odoo instance with mail gateway configured before relying on it in a demo.
- **Rate limiting / abuse protection** on all four endpoints beyond the form's honeypot is
  Phase 6 (Monitoring & Governance).
- **Async execution** — all four triggers still run the workflow synchronously inside the
  HTTP request. A slow node (once Phase 3's AI calls or Phase 4's HTTP Request node exist)
  will hold the connection open until it finishes. Phase 5 moves this to a queue.

## Status: Phase 1 — Core Engine & Builder MVP ✅ Complete

### What was built

**Data model**
- `baramej.flow.workflow` — the workflow container (name, trigger config, draft/published state, versioning)
- `baramej.flow.node` — one step in the graph (type + JSON config + canvas position, reserved for the Phase 1b canvas)
- `baramej.flow.edge` — a connection between two nodes, with `default` / `true` / `false` branch routing
- `baramej.flow.execution` — one run of a workflow, with state and timing
- `baramej.flow.execution.log` — one entry per node executed in a run, with input/output snapshots
- `baramej.flow.credential` — a credential vault, scaffolded now but not yet consumed by any node (that's Phase 3)

**Execution engine** (`engine/`)
- `executor.py` — the graph walker: starts at the trigger node, executes each node, follows the
  right outgoing edge based on what the node returns, and logs every step
- `node_registry.py` — the extension point. Every node type self-registers via a
  `@register_node('type_name')` decorator — adding a new node type never requires touching
  the core walker
- `utils.py` — a `{{dotted.path}}` templating helper used by node configs to reference
  earlier context (e.g. `"name": "{{form.name}}"`)

**Four working node types**
| Node type | What it does |
|---|---|
| `trigger_manual` | Entry point for manual/test runs |
| `action_create_record` | Creates any Odoo record from templated field values |
| `action_update_record` | Updates a record whose id is already in the execution context |
| `condition` | Evaluates one field against a value, routes down the true/false branch |

**UI**
- Structured (list/form-based) workflow builder — Nodes tab, Connections tab, Executions tab
- Execution log viewer with per-node input/output JSON for debugging
- Draft → Published workflow lifecycle with a version counter
- Role-based access: **Flow User** (view/run) vs **Flow Manager** (build/edit/publish/credentials)

**Quality**
- 4 automated tests (`tests/test_workflow_engine.py`) covering: happy-path execution,
  true-branch routing, dead-end-on-false-branch handling, and clear error messages on
  misconfiguration — run with `--test-tags baramej_flow` during install
- One demo workflow (`Demo: Create Contact on Trigger`) installed automatically so the
  module is explorable immediately after install, no setup required
- Every Python file compiles clean; every XML file is well-formed; the node-type registry
  and the model's Selection field were cross-checked to match exactly (see validation notes below)

### What was intentionally deferred (and why)

- **Visual drag-and-drop canvas** — Phase 1 ships a structured list/form builder instead of
  a drag-and-drop canvas. Building an OWL-based canvas is a substantial, standalone frontend
  effort that needs to be built and tested against a running Odoo instance (drag events, SVG
  rendering, and OWL component lifecycle can't be meaningfully validated without one). Rather
  than ship untested frontend JavaScript, the data model already stores `pos_x`/`pos_y` on
  every node specifically so the canvas can be layered on top in Phase 1b without any data
  migration — existing workflows will just render at their stored (or default) position the
  moment the canvas ships.
- **Webhook / email / WhatsApp / form triggers** — the `trigger_type` field on
  `baramej.flow.workflow` already has these as selectable options, but only `manual` and
  `model_event` are functional. Wiring the others up is Phase 2.
- **AI nodes (OpenAI/Gemini)** — the credential vault model exists so admins can start staging
  keys, but no node reads from it yet. That's Phase 3.

## Installation

1. Copy the `baramej_flow` folder into your Odoo 19 `addons` path.
2. Restart the Odoo server with `--update all` or restart and update the apps list.
3. In Settings → Apps, search "Baramej Flow" and install.
4. Go to **Baramej Flow → Workflows**, open the demo workflow, click **Run Test**, then
   check the **Executions** tab to see the per-node log.
5. Assign the **Flow Manager** group (Settings → Users) to whoever will build workflows;
   **Flow User** for anyone who just needs to view/run them.

## Running the test suite

```bash
odoo-bin -d your_db -i baramej_flow --test-enable --test-tags baramej_flow --stop-after-init
```

## Roadmap — what's next

| Phase | Scope | Status |
|---|---|---|
| **1 — Core Engine & Builder MVP** | Data model, execution engine, 4 core node types, structured builder UI | ✅ Complete |
| **2 — Trigger Endpoints** | Generic webhook (HMAC-verified), email gateway trigger, Form Builder + web-form trigger, WhatsApp Business Cloud API webhook | ✅ Complete |
| **3 — AI Decision Layer** | Credential vault goes live, OpenAI + Gemini connector nodes, prompt template editor, structured JSON output parsing, confidence-based routing | ✅ Complete |
| **1b — Visual Canvas** | OWL drag-and-drop canvas layered over the existing node/edge data model | ✅ Complete |
| **Integrations (this round)** | HTTP Request, Database Query, Read Excel, Google OAuth2 + Sheets/Gmail/Calendar/Drive wrappers | ✅ Complete |
| **4 — Advanced Node Library** | HTTP Request, Loop/Iterator, Merge, Wait/Delay, Sub-workflow call, sandboxed Code node, Approval/Human-in-Loop node | Not started |
| **4 — Advanced Node Library** | HTTP Request, Loop/Iterator, Merge, Wait/Delay, Sub-workflow call, sandboxed Code node, Approval/Human-in-Loop node | 3–4 weeks |
| **5 — Execution Engine Hardening** | Move execution onto an async job queue, retry/backoff, try/catch error branches, cycle detection at publish time (replacing today's 500-node safety cap) | 2–3 weeks |
| **6 — Monitoring & Governance** | Analytics dashboard, audit trail, publish-approval gate, webhook rate limiting, AI cost tracking | 2–3 weeks |
| **7 — ITSM Template Library** | Pre-built Ticket Triage, SLA Escalation, CSAT Trigger, WhatsApp/Email-to-Ticket templates, configured against OTA's and Omran's actual ticket categories | 2–3 weeks |
| **8 — UAT, Load Testing & Handover** | End-to-end UAT, webhook load testing, documentation, training | 2–3 weeks |

**Recommended next step:** Phase 4 (Advanced Node Library) — the HTTP Request node in
particular unlocks calling any external API as a plain action, not just AI providers,
and the sandboxed Code node covers whatever edge cases the pre-built nodes don't.
Alternatively, pull Phase 5 (async execution) forward if AI node latency becomes a
real problem on live webhook/WhatsApp traffic before Phase 4 is needed.

---
Developed by **Baramej® Business & Digital Technology Center LLC**.
