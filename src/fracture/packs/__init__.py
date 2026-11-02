"""The pack registry: how a domain is bolted onto the chassis.

A pack is one domain's canonical model, its mart models, its reconciliation
assertions and its departmental views. The chassis -- tenancy, the artifact
store, the bitemporal writer, lineage, reconciliation, the pack compiler and
the assurance engine -- knows nothing about any of it.

The split exists because the margin on a fixed-fee engagement is whatever was
not rebuilt. Everything above `canon.firm` is domain work that belongs to one
vertical; everything below it is plumbing that has already been paid for. A
per-client fork collapses the two and the chassis stops accruing.

Resolution is by name and deliberately lazy: a pack's modules are imported
through `importlib` when first asked for, never by a static import from the
chassis. `assurance` enforces that no chassis module imports a pack, which is
what keeps the seam from rotting back into one layer.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from fracture.core.errors import ConfigError

#: One reconciliation assertion: (table, what must be true, SQL that returns
#: offending rows). Non-empty output fails the mart run.
Assertion = tuple[str, str, str]


@dataclass(frozen=True)
class Entity:
    """One canonical entity: its natural key, its temporality, its numbers.

    This lives in the pack because it is the domain model. The canonical writer
    is chassis and must not know what a household or a time entry is; it needs
    only to be told, for the entity in front of it, which columns make two rows
    the same fact, whether the fact has business-time validity, and which
    columns are numeric.

    That last one is load-bearing rather than cosmetic: populating a numeric
    column from an unconfirmed AI proposal is the forbidden case, and the
    writer can only refuse it if it knows which columns are numbers.
    """

    #: Columns that together identify the fact, beside firm_id.
    key: tuple[str, ...]
    #: True when the fact has business-time validity. A household's address is
    #: true for a period; a cash receipt simply happened.
    business_time: bool = False
    #: Columns carrying figures with financial consequence.
    numeric: frozenset[str] = frozenset()
    #: Source ids in increasing order of authority for this entity. A
    #: lower-authority source never overwrites a higher one; the disagreement is
    #: written to recon.source_variance as a finding instead.
    precedence: tuple[str, ...] = ()


@dataclass(frozen=True)
class FirmRole:
    """What a firm can be to a tenant, and how many may hold that role."""

    max_per_tenant: int | None
    description: str = ""

PACK_ROOT = Path(__file__).parent


@dataclass(frozen=True)
class Pack:
    """A domain pack's manifest.

    `name` is the registry key and the value stored on `control.tenant.pack`, so
    a tenant provisioned against one pack can never be migrated into another by
    accident: the DDL fan-out reads the column.
    """

    name: str
    title: str
    #: One-line statement of what this pack measures, used in the assessment.
    thesis: str
    #: Canonical entity and measure DDL owned by this pack. Numbered 020-039 so
    #: it sorts between the chassis schemas and the chassis bitemporal scripts.
    ddl_dir: Path
    #: Mart models, run in filename order.
    model_dir: Path
    #: Marts that must never be empty. An empty mart is the quietest possible
    #: failure: every downstream figure renders as a zero and nothing raises.
    non_empty: tuple[str, ...]
    assertions: tuple[Assertion, ...]
    #: Dotted path to the module exposing DEPARTMENTS, VIEWS and collect().
    dashboard_module: str
    #: Dotted path to the synthetic estate generator, if this pack ships one.
    synth_module: str | None = None
    #: Adapter verticals whose sources populate this pack.
    verticals: tuple[str, ...] = ("shared",)
    #: Entities an engagement must populate for this pack's marts to work.
    #: Narrower than `entities`, which includes everything the model can hold.
    #: Used by the assessment: an entity on this list that no adapter covers is
    #: priced as manual work rather than omitted, and an omitted entity is the
    #: one that blows the schedule.
    required_entities: tuple[str, ...] = ()
    #: The canonical entities this pack defines, keyed by the entity name an
    #: adapter emits. The canonical writer refuses an entity that is not here,
    #: which is how a typo in an adapter mapping fails loudly.
    entities: Mapping[str, Entity] = field(default_factory=dict)
    #: The firm roles this pack recognises, and how many of each a tenant may
    #: hold. `None` is unlimited; `1` is how "exactly one consolidation root"
    #: is stated. Seeded into `control.pack_firm_role` and enforced there by a
    #: trigger, so the rule survives a second code path.
    firm_roles: Mapping[str, FirmRole] = field(
        default_factory=lambda: {"subject": FirmRole(None, "a firm in scope")}
    )

    def __post_init__(self) -> None:
        for attr in ("ddl_dir", "model_dir"):
            path = getattr(self, attr)
            if not path.is_dir():
                raise ConfigError(f"pack {self.name}: {attr} {path} does not exist")

    # -- content ------------------------------------------------------------

    def ddl_scripts(self) -> list[tuple[str, str]]:
        return [(p.name, p.read_text()) for p in sorted(self.ddl_dir.glob("*.sql"))]

    def dashboard(self) -> Any:
        return importlib.import_module(self.dashboard_module)

    def synth(self) -> Any:
        if self.synth_module is None:
            raise ConfigError(f"pack {self.name} ships no synthetic estate")
        return importlib.import_module(self.synth_module)

    def departments(self) -> tuple[Any, ...]:
        return tuple(self.dashboard().DEPARTMENTS)

    def views(self) -> Mapping[str, Callable[[Any], str]]:
        return self.dashboard().VIEWS

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "thesis": self.thesis,
            "models": [p.stem for p in sorted(self.model_dir.glob("*.sql"))],
            "ddl": [name for name, _ in self.ddl_scripts()],
            "departments": [d.key for d in self.departments()],
            "non_empty": list(self.non_empty),
            "assertions": len(self.assertions),
            "verticals": list(self.verticals),
            "firm_roles": {
                role: spec.max_per_tenant for role, spec in self.firm_roles.items()
            },
            "entities": sorted(self.entities),
        }

    # -- the canonical model ------------------------------------------------

    def required(self) -> tuple[str, ...]:
        return self.required_entities or tuple(sorted(self.entities))

    def entity(self, name: str) -> Entity:
        from fracture.core.errors import FractureError

        try:
            return self.entities[name]
        except KeyError:
            raise FractureError(
                f"pack {self.name!r} defines no canonical entity {name!r}; "
                f"add it to the pack manifest or fix the adapter mapping. "
                f"Known: {', '.join(sorted(self.entities))}"
            ) from None


# -- registry ----------------------------------------------------------------

_REGISTRY: dict[str, Pack] = {}

#: The pack a command runs against when none is named. A tenant always records
#: its own pack; this default only covers the bare CLI and the test fixtures.
DEFAULT_PACK = "wealth"


def register(pack: Pack) -> Pack:
    if pack.name in _REGISTRY:
        raise ConfigError(f"pack {pack.name} is already registered")
    _REGISTRY[pack.name] = pack
    return pack


def _load() -> None:
    """Import the shipped packs once, on first registry access."""
    if _REGISTRY:
        return
    for name in ("wealth", "services"):
        importlib.import_module(f"fracture.packs.{name}")


def get_pack(name: str | None = None) -> Pack:
    _load()
    key = name or DEFAULT_PACK
    try:
        return _REGISTRY[key]
    except KeyError:
        raise ConfigError(
            f"unknown pack {key!r}; registered: {', '.join(sorted(_REGISTRY))}"
        ) from None


def pack_names() -> tuple[str, ...]:
    _load()
    return tuple(sorted(_REGISTRY))


def all_packs() -> tuple[Pack, ...]:
    _load()
    return tuple(_REGISTRY[name] for name in sorted(_REGISTRY))
