"""The code-side gate.

The platform already enforces an AI boundary over *data*: no model computes a
number with financial consequence, held three ways that must agree
(`ai.proposal`, a database trigger on `lineage.ai_edge`, and the canonical
writer). That boundary says nothing about code, and "agent-assisted engineering
with human decision gates and continuous automated audit" is a claim about
code.

This package closes that gap. It is a policy engine over the repository that
answers one question mechanically: is this tree in a state we would ship to a
regulated client? Policies are declarative, the run is reproducible, and the
report is content-hashed the same way a board pack is, so "the gate passed" is
an artifact somebody can be handed rather than an assertion in a standup.

The design follows `adapters/staticcheck.py`, which already proves by AST that
an adapter module cannot write to a source system. That check works because it
is mechanical, cheap and run on every build. Everything here is the same idea
widened to the whole tree.
"""

from fracture.assurance.engine import (
    AssuranceReport,
    AssuranceRun,
    PolicyResult,
    Violation,
    run_policies,
)
from fracture.assurance.policies import POLICIES, Policy, Severity

__all__ = [
    "POLICIES", "Policy", "Severity", "Violation", "PolicyResult",
    "AssuranceReport", "AssuranceRun", "run_policies",
]
