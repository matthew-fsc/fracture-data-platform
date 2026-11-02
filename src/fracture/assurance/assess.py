"""The architecture assessment, computed rather than estimated.

Offering one is a one-to-two week assessment at a fixed fee, credited against
delivery. The deliverable is a current-state review, a target architecture, the
risks and a sequenced roadmap, and the part of it that is usually a guess is
the number at the bottom.

It is not a guess here. Each adapter's `Capabilities` declares the hours to
stand it up and, per canonical entity, how much of that entity it can populate
and what closing the rest costs by hand. A fee therefore falls out of a
coverage manifest. A system we do not support is priced at the cost of a new
adapter rather than dropped from scope, and an entity nothing covers is priced
as manual work rather than omitted, because the omitted line is the one that
blows the schedule.

The output is deliberately boring: numbers, their basis, and the assumptions
that produced them. A client can disagree with a rate or a coverage figure and
the arithmetic survives, which is what makes a fixed fee defensible instead of
merely confident.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Sequence

from fracture.adapters.registry import MANUAL_ENTITY_HOURS, estimate_fold_in
from fracture.core.hashing import canonical_json, sha256_bytes
from fracture.packs import Pack, get_pack

#: Hours to build an adapter for a system nothing supports yet. Deliberately
#: high: the number that gets quoted optimistically is the one that turns a
#: fixed fee into a loss.
NEW_ADAPTER_HOURS = 60.0

#: Default blended rate. A parameter on every call, never read from a config
#: file, because a rate quietly changing between two quotes is worse than a
#: rate someone has to type.
DEFAULT_RATE = 225.0

#: Share of delivery hours the assessment itself represents. The assessment is
#: the first increment of delivery rather than a separate study, which is what
#: makes crediting it against delivery honest rather than a discount.
ASSESSMENT_SHARE = 0.12
ASSESSMENT_MIN_HOURS = 40.0
ASSESSMENT_MAX_HOURS = 80.0


@dataclass(frozen=True)
class Assessment:
    pack: str
    thesis: str
    systems: tuple[str, ...]
    supported: tuple[str, ...]
    unsupported: tuple[str, ...]
    adapter_hours: float
    manual_hours: float
    new_adapter_hours: float
    coverage: float
    entities: tuple[dict[str, Any], ...]
    rate: float
    assessment_hours: float
    generated_at: dt.datetime

    @property
    def delivery_hours(self) -> float:
        return self.adapter_hours + self.manual_hours + self.new_adapter_hours

    @property
    def delivery_fee(self) -> float:
        return round(self.delivery_hours * self.rate, 2)

    @property
    def assessment_fee(self) -> float:
        return round(self.assessment_hours * self.rate, 2)

    @property
    def net_delivery_fee(self) -> float:
        """Delivery less the credited assessment fee."""
        return round(self.delivery_fee - self.assessment_fee, 2)

    @property
    def uncovered(self) -> tuple[str, ...]:
        return tuple(e["entity"] for e in self.entities if e.get("status") != "covered")

    def as_dict(self) -> dict[str, Any]:
        return {
            "pack": self.pack,
            "thesis": self.thesis,
            "generated_at": self.generated_at.isoformat(),
            "systems": {
                "requested": list(self.systems),
                "supported": list(self.supported),
                "unsupported": list(self.unsupported),
            },
            "hours": {
                "existing_adapters": self.adapter_hours,
                "new_adapters": self.new_adapter_hours,
                "manual_entity_work": self.manual_hours,
                "delivery_total": self.delivery_hours,
                "assessment": self.assessment_hours,
            },
            "coverage": self.coverage,
            "uncovered_entities": list(self.uncovered),
            "entities": [dict(e) for e in self.entities],
            "fees": {
                "rate": self.rate,
                "assessment": self.assessment_fee,
                "delivery_gross": self.delivery_fee,
                "assessment_credit": -self.assessment_fee,
                "delivery_net": self.net_delivery_fee,
            },
            "assumptions": [
                f"Blended rate {self.rate:,.0f} per hour.",
                f"A system with no adapter is priced at {NEW_ADAPTER_HOURS:.0f} hours.",
                f"An entity no adapter covers is priced at {MANUAL_ENTITY_HOURS:.0f} "
                "hours of manual work.",
                "The assessment fee is credited in full against delivery.",
                "Hours are fold-in hours against an existing platform, not a build "
                "from zero.",
            ],
        }

    def content_hash(self) -> bytes:
        """Two assessments of the same systems at the same rate hash the same.

        So a quote can be checked against the manifest it came from, months
        later, by anyone who has both.
        """
        payload = self.as_dict()
        payload.pop("generated_at")
        return sha256_bytes(canonical_json(payload))

    def to_json(self) -> str:
        import json

        return json.dumps(self.as_dict(), indent=2, sort_keys=True)

    def to_text(self) -> str:
        lines = [
            f"Architecture assessment -- {self.pack} pack",
            f"  {self.thesis}",
            "",
            f"Systems in scope        {len(self.systems)}"
            f"  ({len(self.supported)} supported, {len(self.unsupported)} not)",
        ]
        if self.unsupported:
            lines.append(f"  no adapter yet        {', '.join(self.unsupported)}")
        lines += [
            f"Canonical coverage      {self.coverage:.1%}",
        ]
        if self.uncovered:
            lines.append(f"  uncovered entities    {', '.join(self.uncovered)}")
        lines += [
            "",
            f"Existing adapter hours  {self.adapter_hours:8.1f}",
            f"New adapter hours       {self.new_adapter_hours:8.1f}",
            f"Manual entity hours     {self.manual_hours:8.1f}",
            f"Delivery hours          {self.delivery_hours:8.1f}",
            "",
            f"Assessment ({self.assessment_hours:.0f}h)      "
            f"{self.assessment_fee:12,.0f}",
            f"Delivery (gross)        {self.delivery_fee:12,.0f}",
            f"Assessment credit       {-self.assessment_fee:12,.0f}",
            f"Delivery (net)          {self.net_delivery_fee:12,.0f}",
            "",
            f"hash {self.content_hash().hex()[:16]}",
        ]
        return "\n".join(lines)


def assess(
    systems: Sequence[str],
    pack: Pack | str | None = None,
    rate: float = DEFAULT_RATE,
    new_adapter_hours: float = NEW_ADAPTER_HOURS,
    generated_at: dt.datetime | None = None,
) -> Assessment:
    resolved = pack if isinstance(pack, Pack) else get_pack(pack)
    estimate = estimate_fold_in(
        systems,
        required_entities=resolved.required(),
        new_adapter_hours=new_adapter_hours,
    )
    raw = estimate.as_dict()

    delivery_before_assessment = (
        raw["adapter_hours"] + raw["manual_hours"] + raw["new_adapter_hours"]
    )
    assessment_hours = min(
        ASSESSMENT_MAX_HOURS,
        max(ASSESSMENT_MIN_HOURS, round(delivery_before_assessment * ASSESSMENT_SHARE)),
    )

    return Assessment(
        pack=resolved.name,
        thesis=resolved.thesis,
        systems=tuple(systems),
        supported=tuple(raw["supported_systems"]),
        unsupported=tuple(raw["unsupported_systems"]),
        adapter_hours=float(raw["adapter_hours"]),
        manual_hours=float(raw["manual_hours"]),
        new_adapter_hours=float(raw["new_adapter_hours"]),
        # Weighted coverage, not the plain count: an entity one adapter covers at
        # 30% is not "covered" in any sense a client would accept.
        coverage=float(raw["weighted_coverage_pct"]),
        entities=tuple(raw["entities"]),
        rate=rate,
        assessment_hours=float(assessment_hours),
        generated_at=generated_at or dt.datetime.now(dt.timezone.utc),
    )
