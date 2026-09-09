# -*- coding: utf-8 -*-
"""The node-executor registry. This is the extension point for every future phase:
adding a new node type is 'write a class, decorate it, register the selection value
on baramej.flow.node' — nothing in the core executor.py graph-walker changes.
"""

_registry = {}


def register_node(node_type):
    """Class decorator. Usage:

        @register_node('action_create_record')
        class ActionCreateRecordExecutor(BaseNodeExecutor):
            def execute(self):
                ...
    """
    def decorator(cls):
        _registry[node_type] = cls
        return cls
    return decorator


def get_node_executor(node_type):
    return _registry.get(node_type)


def get_registered_node_types():
    """Used by tests / diagnostics to confirm the registry and the node_type
    Selection field on baramej.flow.node haven't drifted apart."""
    return sorted(_registry.keys())
