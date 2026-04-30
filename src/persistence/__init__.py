"""Persistence OS — bitemporal, effect-typed cognitive runtime.

Modules:
    fact     — bitemporal 8-tuple datom store             [v0.4.0a1 substrate-primitives]
    effect   — algebraic effect handler stack              [v0.4.0a1 substrate-primitives]
    plan     — EDN plan AST + execute + optimize + promote + MCTS [v0.6.5 shipped]
    replay   — counterfactual trajectory engine            [v0.1.0a1 shipped]
    txn      — atomic multi-datom commit + snapshot isolation [v0.5.1 shipped]
    spec     — parse-don't-validate boundary contracts     [v0.1.0a1 shipped]
    repl     — capability-gated live REPL (WS + browser console) [v0.7.0a1 shipped]
    sdk      — adapter SDK foundation (URI dispatch + stability decorators) [v0.8.0a1 SDK1, in progress]
"""

__version__ = "0.7.0a1"
