"""Adapter registry and the fold-in cost estimator.

The diligence motion is not a separate product: it is this pipeline run against
a throwaway tenant, and its output *is* the fold-in cost estimate (spec 1.1).
That only works if adapter coverage is measurable, which is what this module
makes it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence, Type

from fracture.adapters.base import BaseAdapter, Capabilities, EntityCoverage
from fracture.core.errors import AdapterError
from fracture.core.logging import get_logger

log = get_logger("adapters.registry")

_REGISTRY: dict[str, Type[BaseAdapter]] = {}

#: Canonical entities the platform expects to populate for a wealth firm.
#: An entity on this list with no adapter coverage is manual work, and the
#: estimator prices it as such rather than quietly omitting it.
WEALTH_REQUIRED_ENTITIES: tuple[str, ...] = (
    "party", "household", "household_member", "producer", "book_assignment",
    "account", "balance_snapshot", "fee_schedule", "fee_tier",
    "schedule_assignment", "revenue_event", "invoice", "invoice_line",
    "cash_receipt", "receipt_application", "cost_line", "fte_allocation",
    "service_event",
)

INSURANCE_REQUIRED_ENTITIES: tuple[str, ...] = (
    "party", "household", "producer", "book_assignment", "account",
    "policy_term", "revenue_event", "invoice", "cash_receipt", "service_event",
)

#: Hours to build a canonical entity by hand when no adapter covers it.
MANUAL_ENTITY_HOURS: float = 24.0


def register(cls: Type[BaseAdapter]) -> Type[BaseAdapter]:
    """Class decorator. Registration is what makes an adapter subject to the
    shared test suite, so an unregistered adapter is an untested one."""
    caps = getattr(cls, "capabilities", None)
    if caps is None:
        raise AdapterError(f"{cls.__name__} must declare a `capabilities` manifest")
    if caps.source_id != cls.source_id:
        raise AdapterError(
            f"{cls.__name__}: capabilities.source_id ({caps.source_id!r}) does not match "
            f"source_id ({cls.source_id!r})"
        )
    if cls.source_id in _REGISTRY and _REGISTRY[cls.source_id] is not cls:
        raise AdapterError(f"source_id {cls.source_id!r} is already registered")
    _REGISTRY[cls.source_id] = cls
    return cls


def get_adapter(source_id: str) -> Type[BaseAdapter]:
    load_all()
    if source_id not in _REGISTRY:
        raise AdapterError(
            f"no adapter registered for {source_id!r}; known: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[source_id]


def all_adapters() -> dict[str, Type[BaseAdapter]]:
    load_all()
    return dict(_REGISTRY)


def load_all() -> None:
    """Import every module under `adapters.sources` so decorators run."""
    import importlib
    import pkgutil

    from fracture.adapters import sources

    for module in pkgutil.iter_modules(sources.__path__):
        if not module.name.startswith("_"):
            importlib.import_module(f"{sources.__name__}.{module.name}")


def capability_matrix(source_ids: Sequence[str] | None = None) -> dict[str, Capabilities]:
    adapters = all_adapters()
    ids = source_ids if source_ids is not None else sorted(adapters)
    return {sid: adapters[sid].capabilities for sid in ids if sid in adapters}


# -- fold-in estimate --------------------------------------------------------


@dataclass(frozen=True)
class EntityEstimate:
    entity: str
    best_source: str | None
    completeness: float
    manual_hours: float
    covered: bool
    contributing_sources: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        if not self.covered:
            return "manual"
        if self.completeness >= 0.9:
            return "covered"
        return "partial"


@dataclass
class FoldInEstimate:
    """The diligence deliverable, computed rather than written (spec 13)."""

    target_systems: tuple[str, ...]
    supported_systems: tuple[str, ...]
    unsupported_systems: tuple[str, ...]
    entities: list[EntityEstimate] = field(default_factory=list)
    adapter_hours: float = 0.0
    manual_hours: float = 0.0
    new_adapter_hours: float = 0.0

    @property
    def total_hours(self) -> float:
        return self.adapter_hours + self.manual_hours + self.new_adapter_hours

    @property
    def coverage_pct(self) -> float:
        if not self.entities:
            return 0.0
        return sum(1 for e in self.entities if e.covered) / len(self.entities)

    def weighted_coverage_pct(self) -> float:
        """Coverage weighted by completeness, which is the honest number.

        A source that populates `account` with 40% of its material columns is
        not the same as one that populates it fully, and quoting as if it were
        is how a three-week fold-in becomes five.
        """
        if not self.entities:
            return 0.0
        return sum(e.completeness for e in self.entities) / len(self.entities)

    def as_dict(self) -> dict[str, object]:
        return {
            "target_systems": list(self.target_systems),
            "supported_systems": list(self.supported_systems),
            "unsupported_systems": list(self.unsupported_systems),
            "coverage_pct": round(self.coverage_pct, 4),
            "weighted_coverage_pct": round(self.weighted_coverage_pct(), 4),
            "adapter_hours": self.adapter_hours,
            "manual_hours": self.manual_hours,
            "new_adapter_hours": self.new_adapter_hours,
            "total_hours": self.total_hours,
            "entities": [
                {
                    "entity": e.entity, "status": e.status, "best_source": e.best_source,
                    "completeness": round(e.completeness, 4), "manual_hours": e.manual_hours,
                    "sources": list(e.contributing_sources),
                }
                for e in self.entities
            ],
        }


def estimate_fold_in(
    target_systems: Iterable[str],
    required_entities: Sequence[str] = WEALTH_REQUIRED_ENTITIES,
    new_adapter_hours: float = 60.0,
    manual_entity_hours: float = MANUAL_ENTITY_HOURS,
) -> FoldInEstimate:
    """Given the systems a target runs, compute what we can populate and at what cost.

    Systems with no adapter are priced at `new_adapter_hours` each, not omitted.
    Entities no adapter covers are priced at `manual_entity_hours`. Everything on
    the required list appears in the output, covered or not; an entity that is
    missing from a fold-in quote is the one that blows the schedule.
    """
    adapters = all_adapters()
    targets = tuple(target_systems)
    supported = tuple(s for s in targets if s in adapters)
    unsupported = tuple(s for s in targets if s not in adapters)

    manifests = [adapters[s].capabilities for s in supported]
    adapter_hours = sum(m.fold_in_hours for m in manifests)

    entities: list[EntityEstimate] = []
    manual_hours = 0.0
    for entity in required_entities:
        candidates: list[tuple[str, EntityCoverage]] = [
            (m.source_id, cov)
            for m in manifests
            if (cov := m.coverage_for(entity)) is not None
        ]
        if not candidates:
            entities.append(
                EntityEstimate(entity, None, 0.0, manual_entity_hours, covered=False)
            )
            manual_hours += manual_entity_hours
            continue
        best_source, best = max(candidates, key=lambda c: c[1].completeness)
        # Multiple sources for one entity do not add up to more than one source
        # can give; take the best and add the residual manual effort once.
        residual = best.manual_hours
        manual_hours += residual
        entities.append(
            EntityEstimate(
                entity=entity,
                best_source=best_source,
                completeness=best.completeness,
                manual_hours=residual,
                covered=True,
                contributing_sources=tuple(sorted(s for s, _ in candidates)),
            )
        )

    return FoldInEstimate(
        target_systems=targets,
        supported_systems=supported,
        unsupported_systems=unsupported,
        entities=entities,
        adapter_hours=adapter_hours,
        manual_hours=manual_hours,
        new_adapter_hours=new_adapter_hours * len(unsupported),
    )
