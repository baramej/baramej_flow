# -*- coding: utf-8 -*-


class BaseNodeExecutor:
    """Every node type implements this interface. The executor is instantiated fresh
    for each node execution — no state is kept between nodes except what's explicitly
    passed forward in `context`.

    Subclasses implement execute() and return a dict with:
        'output'  -> the (possibly modified) context dict to pass to the next node
        'branch'  -> optional; which outgoing edge to follow (e.g. 'true'/'false').
                     Omit or return None to follow the 'default' edge.
        'pause'   -> optional; return True to pause the execution here (state
                     becomes 'waiting') instead of continuing to the next node.
                     Used by nodes waiting on something external — a human
                     decision (human_approval), a resubmission
                     (wait_for_resubmission), or an event like payment
                     confirmation (send_payment_link). The engine records exactly
                     this node as execution.waiting_node_id; a later call to
                     WorkflowEngine.resume(execution, branch=...) picks the
                     outgoing edge and continues from here — possibly in a
                     completely different request, hours or days later.

    Raise any exception to mark the node (and the whole execution) as failed — the
    engine catches it, logs the message, and stops the run. Phase 4 will add
    try/catch-style error-handling edges so a node can define its own failure path
    instead of always stopping the run.
    """

    def __init__(self, env, node, context):
        self.env = env
        self.node = node
        self.context = context

    def execute(self):
        raise NotImplementedError
