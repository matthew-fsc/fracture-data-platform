"""Warehouse decision Fracture OS should read instead of assuming one.

The operating platform is Postgres. This module does not open a connection
and does not ship an evaluation harness.
"""

from __future__ import annotations


def warehouse_decision() -> dict:
    return {
        "engine": "postgres",
        "version": "16",
        "tenancy": "database_per_tenant",
        "source": "docs/spec-v1.md",
    }
