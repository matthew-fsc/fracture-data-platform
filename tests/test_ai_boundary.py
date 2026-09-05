"""The AI boundary (spec section 8).

AI drafts, extracts and summarises. It never computes a number with financial
consequence. Three independent enforcement points have to agree, and each is
tested here on its own, because a boundary enforced in only one of them is a
boundary one import statement wide.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import psycopg2
import pytest

from fracture.adapters.base import CanonicalRecord
from fracture.ai import boundary
from fracture.canon.writer import CanonWriter
from fracture.core import db
from fracture.core.errors import AIBoundaryViolation
from fracture.ingest.lineage import LineageWriter, RawRef
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

T = dt.datetime(2026, 3, 31, 12, 0, tzinfo=dt.timezone.utc)


def _ref(control, tenant) -> RawRef:
    from fracture.control.provisioning import ensure_streams

    ensure_streams(control, tenant, [("orion", "positions")])
    load_id = uuid.uuid4()
    with control.tenant_connection(tenant, "owner") as conn:
        db.execute(
            conn,
            "insert into raw._load (load_id, firm_id, source_id, stream, extracted_at, "
            "artifact_uri, artifact_sha256, row_count) "
            "values (%s,'F1','orion','positions', now(), 's3://fixture', '\\x00', 1)",
            (load_id,),
        )
    return RawRef(load_id=load_id, sequence=1)


def _proposal(conn, materiality=None, kind="extraction") -> uuid.UUID:
    return boundary.record_proposal(
        conn,
        kind=kind,
        model="claude-fixture",
        prompt="Extract the commission amount from this statement.",
        input_refs={"artifact_uris": ["s3://fixture/statement.pdf"]},
        output={"amount": "84000.00"},
        materiality=materiality,
    )


# -- point 1: nothing enters except as a proposal -----------------------------


def test_proposal_records_model_and_prompt_hash_not_the_prompt(control, fresh_tenant):
    """The audit question is "was this the same prompt", not "what did it say" --
    and the prompt may carry client data."""
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        proposal_id = _proposal(conn)
        row = db.query_one(
            conn, "select * from ai.proposal where proposal_id = %s", (proposal_id,)
        )
    assert row["model"] == "claude-fixture"
    assert len(bytes(row["prompt_hash"])) == 32
    assert "Extract the commission" not in str(row)


def test_a_service_account_cannot_confirm_its_own_proposal(control, fresh_tenant):
    """A system confirming its own proposals is the boundary with extra steps."""
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        proposal_id = _proposal(conn)
        with pytest.raises(AIBoundaryViolation, match="must name a person"):
            boundary.confirm(conn, proposal_id, "system:pipeline")


def test_a_rejected_proposal_cannot_then_be_confirmed(control, fresh_tenant):
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        proposal_id = _proposal(conn)
        boundary.reject(conn, proposal_id, "the figure did not match the statement")
        with pytest.raises(AIBoundaryViolation, match="already rejected"):
            boundary.confirm(conn, proposal_id, "matthew")


def test_confirmed_and_rejected_are_mutually_exclusive_in_the_database(control, fresh_tenant):
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        proposal_id = _proposal(conn)
        boundary.confirm(conn, proposal_id, "matthew")
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        with pytest.raises(psycopg2.errors.CheckViolation):
            db.execute(
                conn,
                "update ai.proposal set rejected_reason = 'changed my mind' "
                "where proposal_id = %s",
                (proposal_id,),
            )


# -- point 2: the database trigger -------------------------------------------


def test_database_refuses_a_numeric_column_from_an_unconfirmed_proposal(control, fresh_tenant):
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        proposal_id = _proposal(conn, materiality=Decimal("84000"))
        with pytest.raises(psycopg2.errors.CheckViolation, match="ai boundary"):
            boundary.attach(
                conn, proposal_id, "canon.revenue_event", "1", [("amount", True)]
            )


def test_database_allows_a_non_numeric_column_from_an_unconfirmed_proposal(control, fresh_tenant):
    """Field mapping and classification proposals are the permitted use."""
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        proposal_id = _proposal(conn, kind="field_mapping")
        boundary.attach(
            conn, proposal_id, "canon.account", "1", [("account_subtype", False)]
        )
        assert boundary.violations(conn) == []


def test_database_allows_a_numeric_column_once_a_human_confirms(control, fresh_tenant):
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        proposal_id = _proposal(conn, materiality=Decimal("84000"))
        boundary.confirm(conn, proposal_id, "matthew")
        boundary.attach(conn, proposal_id, "canon.revenue_event", "1", [("amount", True)])
        assert boundary.violations(conn) == []


def test_below_the_materiality_threshold_a_transcription_may_flow(control, fresh_tenant):
    """Extraction from a commission PDF is a transcription with an artifact
    behind it; above a per-tenant threshold it still needs a human."""
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        boundary.set_materiality_threshold(conn, "extraction", Decimal("250"))
        small = _proposal(conn, materiality=Decimal("40"))
        boundary.attach(conn, small, "canon.revenue_event", "10", [("amount", True)])
        assert boundary.violations(conn) == []

        large = _proposal(conn, materiality=Decimal("5000"))
        with pytest.raises(psycopg2.errors.CheckViolation):
            boundary.attach(conn, large, "canon.revenue_event", "11", [("amount", True)])


def test_a_proposal_that_does_not_exist_is_refused(control, fresh_tenant):
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        with pytest.raises(psycopg2.Error):
            boundary.attach(
                conn, uuid.uuid4(), "canon.revenue_event", "1", [("amount", True)]
            )


# -- point 3: the canonical writer -------------------------------------------


def test_writer_refuses_an_unconfirmed_proposal_on_a_numeric_column(control, fresh_tenant):
    """The caller gets a sentence naming the column, not a constraint violation
    from three frames down."""
    ref = _ref(control, fresh_tenant)
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        proposal_id = _proposal(conn, materiality=Decimal("84000"))
        record = CanonicalRecord(
            entity="revenue_event", natural_key="RE-1", firm_id="F1",
            values={
                "revenue_event_id": "RE-1", "event_type": "commission",
                "period_start": dt.date(2026, 1, 1), "period_end": dt.date(2026, 3, 31),
                "amount": Decimal("84000.00"),
            },
            refs=(ref,), source_id="manual_fee_schedule", ai_proposal_id=proposal_id,
            valid_from=dt.date(2026, 1, 1),
        )
        with LineageWriter(conn) as lineage:
            with pytest.raises(AIBoundaryViolation, match="amount"):
                CanonWriter(conn, lineage, system_time=T).write([record])


def test_writer_allows_a_confirmed_proposal(control, fresh_tenant):
    ref = _ref(control, fresh_tenant)
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        proposal_id = _proposal(conn, materiality=Decimal("84000"))
        boundary.confirm(conn, proposal_id, "matthew")
        record = CanonicalRecord(
            entity="revenue_event", natural_key="RE-2", firm_id="F1",
            values={
                "revenue_event_id": "RE-2", "event_type": "commission",
                "period_start": dt.date(2026, 1, 1), "period_end": dt.date(2026, 3, 31),
                "amount": Decimal("84000.00"),
            },
            refs=(ref,), source_id="manual_fee_schedule", ai_proposal_id=proposal_id,
            valid_from=dt.date(2026, 1, 1),
        )
        with LineageWriter(conn) as lineage:
            stats = CanonWriter(conn, lineage, system_time=T).write([record])
        assert stats.inserted == 1
        boundary.assert_no_violations(conn)
        edges = db.query(
            conn,
            "select target_column, is_numeric from lineage.ai_edge "
            "where target_table = 'canon.revenue_event'",
        )
        assert any(e["target_column"] == "amount" and e["is_numeric"] for e in edges)


def test_standing_check_is_clean_on_a_normal_load(control, loaded_estate):
    """No pipeline path should ever produce a violation."""
    with control.tenant_connection(loaded_estate["tenant"], "transform") as conn:
        boundary.assert_no_violations(conn)


def test_pending_queue_is_the_fold_in_review_list(control, fresh_tenant):
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        first = _proposal(conn, kind="field_mapping")
        second = _proposal(conn, kind="field_mapping")
        boundary.confirm(conn, first, "matthew")
        waiting = [p["proposal_id"] for p in boundary.pending(conn, kind="field_mapping")]
        assert second in waiting
        assert first not in waiting
