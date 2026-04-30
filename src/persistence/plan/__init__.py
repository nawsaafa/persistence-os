"""persistence.plan — homoiconic plan AST module (v0.3.0a1).

Commits to three claims (see docs/plans/2026-04-23-persistence-plan-v0.1-design.md):
1. Plans are content-addressed Merkle DAGs
2. Parse round-trips byte-identical
3. Spec validation catches malformed plans

v0.3.0a1 closes R3-M4 (deferred from v0.2.0a1 ARIS gate): a coercion
registry that lets authors put ``datetime``, ``Decimal``, ``UUID``,
``bytes``, ``frozenset``, and EDN symbols in attrs without breaking
``Node.id``. See docs/plans/2026-04-24-r3-m4-coercion-registry-design.md.
"""
from __future__ import annotations

from persistence.plan._ast import (
    ID_HEX_WIDTH,
    PLAN_CANONICAL_VERSION,
    Node,
)
from persistence.plan._coerce import (
    Coercion,
    lookup_coercion,
    register_coercion,
    unregister_coercion,
)
from persistence.plan._edit import (
    delete_step,
    edit_step,
    insert_step_after,
    insert_step_before,
)
from persistence.plan._errors import (
    EvaluatorContractError,
    ExpanderContractError,
    GateFailure,
    MetricNotRegistered,
    OptimizerNotAvailable,
    ParseError,
    PlanDepthExceeded,
    PlanEditDownstreamExecuted,
    PlanEditOutsideDosync,
    StepIdNotFound,
    UnimplementedNodeKindError,
)
from persistence.plan._walk import walk
from persistence.plan._dispatch import Dispatcher, Handler
from persistence.plan._execute import (
    ExecutionResult,
    FailureInfo,
    LeafResult,
    TrainingExample,
    execute,
)
from persistence.plan._metric_registry import (
    MetricRef,
    lookup_metric,
    register_metric,
    unregister_metric,
)
from persistence.plan._optimize import OptimizedPlan, optimize
from persistence.plan._parse import parse, unparse
from persistence.plan._promotion import (
    PromotionRecord,
    ReplayEngine,
    gate_g1_replay_byte_identity,
    gate_g2_audit_chain,
    gate_g3_score_delta,
    gate_g4_stub,
    promote,
)
from persistence.plan._skill_library import SkillLibrary
from persistence.plan._mcts import (
    Action,
    AddStepAction,
    ComposeWithSkillAction,
    Evaluator,
    Expander,
    LLMExpander,
    LLMJudgeEvaluator,
    MAX_PLAN_DEPTH,
    MCTSConfig,
    MCTSEdge,
    MCTSNode,
    MCTSResult,
    SubstituteLeafAction,
    apply_action,
    mcts_search,
)
from persistence.plan._mcts_promote import (
    MCTSPromotionResult,
    mcts_promote,
)

__all__ = [
    "Action",
    "AddStepAction",
    "Coercion",
    "ComposeWithSkillAction",
    "Dispatcher",
    "Evaluator",
    "EvaluatorContractError",
    "ExecutionResult",
    "Expander",
    "ExpanderContractError",
    "FailureInfo",
    "GateFailure",
    "Handler",
    "ID_HEX_WIDTH",
    "LLMExpander",
    "LLMJudgeEvaluator",
    "LeafResult",
    "MAX_PLAN_DEPTH",
    "MCTSConfig",
    "MCTSEdge",
    "MCTSNode",
    "MCTSPromotionResult",
    "MCTSResult",
    "MetricNotRegistered",
    "MetricRef",
    "Node",
    "OptimizedPlan",
    "OptimizerNotAvailable",
    "ParseError",
    "PLAN_CANONICAL_VERSION",
    "PlanDepthExceeded",
    "PlanEditDownstreamExecuted",
    "PlanEditOutsideDosync",
    "PromotionRecord",
    "ReplayEngine",
    "SkillLibrary",
    "StepIdNotFound",
    "SubstituteLeafAction",
    "TrainingExample",
    "UnimplementedNodeKindError",
    "apply_action",
    "delete_step",
    "edit_step",
    "execute",
    "gate_g1_replay_byte_identity",
    "gate_g2_audit_chain",
    "gate_g3_score_delta",
    "gate_g4_stub",
    "insert_step_after",
    "insert_step_before",
    "lookup_coercion",
    "lookup_metric",
    "mcts_promote",
    "mcts_search",
    "optimize",
    "parse",
    "promote",
    "register_coercion",
    "register_metric",
    "unparse",
    "unregister_coercion",
    "unregister_metric",
    "walk",
]
