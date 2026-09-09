# -*- coding: utf-8 -*-
{
    'name': 'Baramej Flow - Workflow Automation Engine',
    'version': '19.0.1.0.0',
    'category': 'Productivity',
    'summary': 'Visual, node-based workflow automation engine, built natively for Odoo 19 (Phase 1: Core Engine & Builder MVP)',
    'description': """
Baramej Flow
============
A custom, industrial-grade workflow automation engine built natively on Odoo 19 —
Baramej's proprietary alternative to bolting on a third-party tool like n8n or Zapier.

Phase 1 (this release) delivers:
--------------------------------
* Core data model: Workflows, Nodes, Connections (Edges), Executions, Execution Logs
* An extensible node-executor engine — new node types register themselves without
  touching the core graph-walking logic
* Three foundational node types:
    - Manual Trigger
    - Action: Create Record
    - Action: Update Record
    - Condition (IF branch, true/false routing)
* A structured (list/form based) workflow builder UI
* Full per-node execution logging, with input/output snapshots for debugging and replay
* Draft / Published workflow states with version tracking
* Role-based access control (Flow User / Flow Manager)
* Automated test suite covering the execution engine and conditional branching
* A ready-to-run demo workflow

Phase 2 (this release) adds:
-----------------------------
* Generic HMAC-signed inbound webhook trigger
* WhatsApp Business Cloud API webhook (Meta verification handshake + incoming messages)
* Incoming email trigger via a dedicated mail.alias per workflow
* Web Form Builder — drag-free field designer, publish/unpublish, public URL,
  spam honeypot, submission tracking
* Four new trigger node types (Webhook / Email / WhatsApp / Form) matching the
  existing Manual trigger pattern

Phase 3 (this release) adds:
-----------------------------
* Credential vault goes live — "Test Connection" action validates a key against the
  real provider before any workflow relies on it
* AI: OpenAI node — Chat Completions API, JSON-mode structured output
* AI: Google Gemini node — Gemini API, JSON-mode structured output
* Prompt templates on both AI nodes support {{dotted.path}} variable interpolation,
  same syntax as every other node
* Confidence-threshold / classification-based branching — reuses the existing
  Condition node (now supports dotted-path field lookup) rather than adding a new
  node type, so context['ai_result']['confidence'] can drive a true/false branch
  directly
* Per-node AI token usage tracked in the execution context for later cost reporting

E-Service Ticket pattern (this release):
-----------------------------------------
* Pause/resume execution engine — a node can now stop a workflow mid-graph and
  wait, potentially days later, for a human decision or external event
* baramej.flow.ticket — a portal-visible case object (kanban by status, chatter,
  mail.thread) created automatically when a logged-in citizen submits a
  require_login form
* 4 new node types: Assign Employee, Human Approval (pauses for
  approve/reject/request-info), Wait For Resubmission (pauses for the applicant
  to add more info), Send Payment Link (pauses until payment confirmed)
* Portal pages: /my/eservices (list) and /my/eservices/<id> (status, chatter,
  resubmission form)
* Login is enforced before submission on require_login forms (default True);
  existing non-ticketed forms (require_login=False) keep working unchanged

Planned in upcoming phases (see module README for the full roadmap):
----------------------------------------------------------------------
* File upload support in the Form Builder (needed for real document-heavy
  e-services like the Taxi Plate Registration example)
* Payment confirmation via direct webhook instead of polling
* Phase 4 remainder: Loop/Merge/Sub-workflow/sandboxed Code nodes
* Phase 4 — Advanced node library: HTTP Request, Loop, Merge, Wait/Delay, Sub-workflow
  call, sandboxed Code node, Human-in-the-Loop approval node
* Phase 5 — Async queue-based execution, retries, error-handling branches, sandbox
  test-run mode
* Phase 6 — Analytics dashboard, audit trail, webhook rate limiting/signature
  verification, AI cost tracking
* Phase 7 — Pre-built ITSM templates (ticket triage, SLA escalation, CSAT trigger)

Developed by Baramej (R) Business & Digital Technology Center LLC.
    """,
    'author': 'Baramej Business & Digital Technology Center LLC',
    'website': 'https://www.baramej.om',
    'license': 'OPL-1',
    'depends': ['base', 'mail', 'web', 'portal', 'payment'],
    'external_dependencies': {
        'python': ['psycopg2', 'openpyxl'],
    },
    'assets': {
        'web.assets_backend': [
            'baramej_flow/static/src/js/flow_canvas.js',
            'baramej_flow/static/src/xml/flow_canvas.xml',
            'baramej_flow/static/src/scss/flow_canvas.scss',
        ],
    },
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'data/ir_cron_data.xml',
        'views/workflow_definition_views.xml',
        'views/workflow_node_views.xml',
        'views/workflow_edge_views.xml',
        'views/workflow_execution_views.xml',
        'views/workflow_credential_views.xml',
        'views/workflow_form_views.xml',
        'views/eservice_ticket_views.xml',
        'views/portal_templates.xml',
        'views/menu_views.xml',
    ],
    'demo': [
        'demo/demo_workflow.xml',
        'demo/demo_eservice_workflow.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
