"""Core primitives: redaction, hashing, migrations, the fold-in estimator."""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal

import pytest

from fracture.adapters.parsing import as_date, as_decimal, last4, optional_text
from fracture.adapters.registry import estimate_fold_in
from fracture.core.errors import AdapterError
from fracture.core.redaction import Redactor, RedactingFilter, contains_pii, redact


# -- redaction ---------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "SSN is 123-45-6789",
        "contact amara@example.com about it",
        "call +1 (415) 555-0142",
        "Bearer eyJhbGciOiJIUzI1NiJ9",
        "card 4111 1111 1111 1111",
        "EIN 98-7654321",
    ],
)
def test_value_shapes_are_redacted_wherever_they_appear(value):
    """A source that names its SSN column `field_17` still emits something that
    looks like an SSN."""
    assert contains_pii(value)
    assert "REDACTED" in redact(value)


@pytest.mark.parametrize(
    "key", ["ssn", "taxId", "date_of_birth", "account_number", "api_key", "home_street"]
)
def test_sensitive_keys_are_redacted_by_name(key):
    scrubbed = redact({key: "anything at all"})
    assert scrubbed[key] == "[REDACTED]"


def test_redaction_reaches_into_nested_structures():
    payload = {"party": {"contacts": [{"email": "a@b.com", "ssn": "123-45-6789"}]}}
    scrubbed = redact(payload)
    assert scrubbed["party"]["contacts"][0]["email"] == "[REDACTED]"
    assert scrubbed["party"]["contacts"][0]["ssn"] == "[REDACTED]"
    assert not contains_pii(scrubbed)


def test_ordinary_business_data_is_left_alone():
    """Over-redaction that eats account ids and amounts makes logs useless, and
    a useless log gets turned off."""
    payload = {"account_id": "A-00042", "market_value": "1450000.00", "firm_id": "MWP"}
    assert redact(payload) == payload
    assert not contains_pii(payload)


def test_logging_filter_scrubs_the_formatted_message():
    logger = logging.getLogger("fracture.test.redaction")
    logger.handlers.clear()
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.info("mapping party %s with ssn %s", "P-1", "123-45-6789")
    assert records
    assert "123-45-6789" not in records[0].getMessage()


def test_redactor_reports_masked_values_as_clean():
    assert not Redactor().contains_pii({"ssn": "[REDACTED]"})


# -- parsing -----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1,234.50", "1234.50"),
        ("$1,234.50", "1234.50"),
        ("(1,234.50)", "-1234.50"),
        ("1234.50-", "-1234.50"),
        (1234.5, "1234.5"),
        (Decimal("1234.50"), "1234.50"),
    ],
)
def test_money_formats_from_real_file_feeds(raw, expected):
    assert as_decimal(raw, "amount") == Decimal(expected)


def test_an_unparseable_amount_raises_rather_than_becoming_zero():
    """A zero that should have been $84,000 reconciles to nothing and nobody
    notices."""
    with pytest.raises(AdapterError, match="cannot parse"):
        as_decimal("n/a", "premium")


def test_an_empty_amount_raises_unless_a_default_is_stated():
    with pytest.raises(AdapterError, match="expected a number"):
        as_decimal("", "premium")
    assert as_decimal("", "premium", default=Decimal(0)) == Decimal(0)


def test_a_boolean_is_not_a_number():
    with pytest.raises(AdapterError, match="boolean is not a number"):
        as_decimal(True, "amount")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-03-31", dt.date(2026, 3, 31)),
        ("03/31/2026", dt.date(2026, 3, 31)),
        ("20260331", dt.date(2026, 3, 31)),
        ("2026-03-31T14:00:00Z", dt.date(2026, 3, 31)),
        ("31-Mar-2026", dt.date(2026, 3, 31)),
    ],
)
def test_date_formats_from_real_file_feeds(raw, expected):
    assert as_date(raw, "as_of") == expected


def test_an_unparseable_date_raises():
    with pytest.raises(AdapterError, match="cannot parse"):
        as_date("last Tuesday", "as_of")


def test_last4_keeps_only_four_digits():
    assert last4("123-45-6789") == "6789"
    assert last4("6789") == "6789"
    assert last4("12") is None
    assert last4(None) is None


def test_optional_text_treats_blank_as_absent():
    assert optional_text("   ") is None
    assert optional_text("core") == "core"


# -- the fold-in estimator ---------------------------------------------------


def test_unsupported_systems_are_priced_not_omitted():
    """An entity missing from a fold-in quote is the one that blows the
    schedule."""
    estimate = estimate_fold_in(["orion", "addepar", "black_diamond"], new_adapter_hours=60)
    assert estimate.unsupported_systems == ("addepar", "black_diamond")
    assert estimate.new_adapter_hours == 120
    assert estimate.total_hours > estimate.adapter_hours


def test_uncovered_entities_are_priced_as_manual():
    estimate = estimate_fold_in(["schwab_custodian"])
    manual = [e for e in estimate.entities if not e.covered]
    assert manual, "a custodian file alone cannot populate the whole canonical model"
    assert estimate.manual_hours >= len(manual) * 24


def test_every_required_entity_appears_in_the_estimate():
    from fracture.adapters.registry import WEALTH_REQUIRED_ENTITIES

    estimate = estimate_fold_in(["orion"])
    assert {e.entity for e in estimate.entities} == set(WEALTH_REQUIRED_ENTITIES)


def test_weighted_coverage_is_lower_than_naive_coverage():
    """A source that populates 40% of an entity's columns is not the same as one
    that populates it fully, and quoting as if it were is how three weeks
    becomes five."""
    estimate = estimate_fold_in(
        ["orion", "redtail", "schwab_custodian", "qbo", "manual_fee_schedule"]
    )
    assert estimate.coverage_pct == 1.0
    assert estimate.weighted_coverage_pct() < 1.0


def test_more_sources_never_reduce_coverage():
    small = estimate_fold_in(["orion"])
    large = estimate_fold_in(["orion", "redtail", "qbo", "manual_fee_schedule"])
    assert large.coverage_pct >= small.coverage_pct


def test_registering_two_adapters_with_one_id_is_refused():
    from fracture.adapters.base import BaseAdapter, Capabilities, EntityCoverage
    from fracture.adapters.registry import register

    class Duplicate(BaseAdapter):
        source_id = "orion"
        capabilities = Capabilities(
            source_id="orion", vertical="wealth", delivery="api",
            entities=(EntityCoverage("party", "person", 0.5),),
        )

    with pytest.raises(AdapterError, match="already registered"):
        register(Duplicate)


def test_a_capability_manifest_must_match_its_adapter():
    from fracture.adapters.base import BaseAdapter, Capabilities, EntityCoverage
    from fracture.adapters.registry import register

    class Mismatched(BaseAdapter):
        source_id = "brand_new"
        capabilities = Capabilities(
            source_id="something_else", vertical="wealth", delivery="api",
            entities=(EntityCoverage("party", "person", 0.5),),
        )

    with pytest.raises(AdapterError, match="does not match"):
        register(Mismatched)


def test_completeness_outside_zero_to_one_is_refused():
    from fracture.adapters.base import EntityCoverage

    with pytest.raises(AdapterError, match="between 0 and 1"):
        EntityCoverage("party", "person", 1.5)
