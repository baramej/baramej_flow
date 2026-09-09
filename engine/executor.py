# -*- coding: utf-8 -*-
"""The core execution engine: walks a workflow's node graph from its trigger node,
executing one node at a time, following edges based on each node's returned branch.

As of the E-Service pattern, the engine supports PAUSING mid-graph and RESUMING
later — needed for anything that waits on a human action (approve/reject/request-
info) or an external event (payment confirmation). A node signals a pause by
returning {'pause': True, ...} from execute() (see engine/nodes/base.py). When that
happens, the engine stops walking, marks the execution 'waiting', records exactly
which node it paused at, and snapshots the live context so `resume()` can pick up
from there — possibly minutes, days, or weeks later, in a completely different
request/transaction.

Still fully synchronous within each run()/resume() call — a long AI or HTTP call
still holds its own request open. That's unrelated to pausing: pausing is about
waiting on something that happens *outside* this process entirely (a person
clicking a button, a payment webhook), not about not blocking the current request.
Phase 5's async queue is still the answer for the "don't block on a slow API call"
problem; this solves a different problem.
"""
import json
import logging
from datetime import datetime

from odoo import _, fields
from odoo.exceptions import UserError

from .node_registry import get_node_executor

_logger = logging.getLogger(__name__)

# Hard cap on nodes visited per run/resume. This is a blunt safety net against an
# accidentally-cyclic graph (e.g. a condition edge looped back on itself) — Phase 5's
# hardening pass replaces this with proper cycle detection at publish time, so a bad
# graph is rejected before it can ever run rather than caught mid-execution.
_MAX_NODES_PER_RUN = 500


class WorkflowEngine:

    def __init__(self, env):
        # Workflows execute with their own authority, not the calling user's.
        # Odoo 19's refactored ORM removed Environment.sudo() (recordset-only now);
        # env(su=True) is the environment-level equivalent.
        self.env = env(su=True)

    def run(self, workflow, trigger_source='manual', initial_context=None):
        """Starts a brand new execution from `workflow`'s trigger node. Returns the
        baramej.flow.execution record regardless of outcome — success, failed, or
        waiting (paused) — so callers inspect execution.state either way."""
        execution = self.env['baramej.flow.execution'].create({
            'workflow_id': workflow.id,
            'trigger_source': trigger_source,
            'context_json': json.dumps(initial_context or {}, default=str),
            'live_context_json': json.dumps(initial_context or {}, default=str),
            'state': 'running',
        })

        start_node = self._find_start_node(workflow)
        if not start_node:
            execution.write({
                'state': 'failed',
                'end_date': fields.Datetime.now(),
                'error_message': _('Workflow "%s" has no nodes to start from.') % workflow.name,
            })
            return execution

        return self._walk(execution, start_node, dict(initial_context or {}))

    def resume(self, execution, branch=None, context_update=None):
        """Continues a paused ('waiting') execution from exactly where it stopped.

        `branch` selects which outgoing edge of the waiting node to follow — e.g.
        'approved' / 'rejected' / 'more_info' for a human_approval node. Leave as
        None for nodes with only a single 'default' outgoing edge (e.g.
        wait_for_resubmission, send_payment_link).

        `context_update` is merged into the execution's live context before
        continuing — e.g. {'rejection_reason': '...'} or resubmitted form data.
        """
        if execution.state != 'waiting':
            raise UserError(_('Execution %s is not waiting (state=%s) — nothing to resume.') % (
                execution.id, execution.state))
        if not execution.waiting_node_id:
            raise UserError(_('Execution %s has no recorded waiting node — cannot resume.') % execution.id)

        context = json.loads(execution.live_context_json or '{}')
        context.update(context_update or {})

        waiting_node = execution.waiting_node_id
        next_node = self._find_next_node(waiting_node, branch)

        execution.write({'state': 'running', 'waiting_node_id': False})

        if not next_node:
            # The waiting node's chosen branch has nothing wired after it — that's
            # a valid, deliberate dead end (e.g. a rejection with no further
            # automated steps), not an error.
            execution.write({
                'state': 'success',
                'end_date': fields.Datetime.now(),
                'live_context_json': json.dumps(context, default=str),
            })
            return execution

        return self._walk(execution, next_node, context)

    def _walk(self, execution, start_node, context):
        """Shared graph-walking loop used by both run() and resume(). Node
        execution-log sequence numbers continue from wherever the execution's
        existing logs left off, so a paused-then-resumed execution's log reads as
        one continuous sequence, not two separate numbering runs."""
        sequence_offset = len(execution.log_ids)

        try:
            current_node = start_node
            visited = 0
            while current_node and visited < _MAX_NODES_PER_RUN:
                visited += 1
                context, branch, log_vals, paused = self._execute_node(current_node, context)
                log_vals.update({'execution_id': execution.id, 'sequence': sequence_offset + visited})
                self.env['baramej.flow.execution.log'].create(log_vals)

                if log_vals['state'] == 'failed':
                    execution.write({
                        'state': 'failed',
                        'end_date': fields.Datetime.now(),
                        'error_message': log_vals.get('error_message'),
                        'live_context_json': json.dumps(context, default=str),
                    })
                    return execution

                if paused:
                    execution.write({
                        'state': 'waiting',
                        'waiting_node_id': current_node.id,
                        'live_context_json': json.dumps(context, default=str),
                    })
                    return execution

                current_node = self._find_next_node(current_node, branch)

            if visited >= _MAX_NODES_PER_RUN:
                execution.write({
                    'state': 'failed',
                    'end_date': fields.Datetime.now(),
                    'error_message': _('Execution stopped after %s nodes — likely a cyclic connection. '
                                        'Check the workflow\'s edges.') % _MAX_NODES_PER_RUN,
                    'live_context_json': json.dumps(context, default=str),
                })
                return execution

            execution.write({
                'state': 'success',
                'end_date': fields.Datetime.now(),
                'live_context_json': json.dumps(context, default=str),
            })

        except Exception as exc:  # noqa: BLE001 - deliberately broad: any failure here
            # must still leave a readable execution record, not a raw traceback.
            _logger.exception('Baramej Flow: execution %s failed', execution.id)
            execution.write({
                'state': 'failed',
                'end_date': fields.Datetime.now(),
                'error_message': str(exc),
            })

        return execution

    def _find_start_node(self, workflow):
        trigger_nodes = workflow.node_ids.filtered(lambda n: n.node_type.startswith('trigger'))
        if trigger_nodes:
            return trigger_nodes[0]
        # Fall back to the lowest-sequence node so a workflow missing an explicit
        # trigger node still runs during early testing, rather than failing outright.
        return workflow.node_ids[:1]

    def _execute_node(self, node, context):
        executor_cls = get_node_executor(node.node_type)
        log_vals = {
            'node_id': node.id,
            'node_name': node.name,
            'node_type': node.node_type,
            'input_json': json.dumps(context, default=str),
        }

        if not executor_cls:
            log_vals.update({
                'state': 'failed',
                'error_message': _('No executor is registered for node type "%s". This usually means the node '
                                    'type was added to the Selection field but the matching engine/nodes/*.py '
                                    'file was never registered.') % node.node_type,
            })
            return context, None, log_vals, False

        started_at = datetime.now()
        try:
            executor = executor_cls(self.env, node, context)
            result = executor.execute()
            duration = (datetime.now() - started_at).total_seconds()

            new_context = result.get('output', context)
            branch = result.get('branch')
            paused = bool(result.get('pause'))

            log_vals.update({
                'state': 'waiting' if paused else 'success',
                'output_json': json.dumps(new_context, default=str),
                'duration': duration,
            })
            return new_context, branch, log_vals, paused

        except Exception as exc:  # noqa: BLE001 - node-level failures must be caught
            # individually so the execution log shows exactly which node broke, not
            # just "the workflow failed somewhere".
            duration = (datetime.now() - started_at).total_seconds()
            log_vals.update({
                'state': 'failed',
                'error_message': str(exc),
                'duration': duration,
            })
            return context, None, log_vals, False

    def _find_next_node(self, node, branch):
        edges = node.outgoing_edge_ids
        if branch is not None:
            matching = edges.filtered(lambda e: e.condition_branch == branch)
            if matching:
                return matching[0].target_node_id
        default_edges = edges.filtered(lambda e: e.condition_branch == 'default')
        if default_edges:
            return default_edges[0].target_node_id
        return edges[:1].target_node_id if edges else None
