"""Guard-railed AI entry point for Fracture OS.

`describe` is the contract and does not touch a database. `record_proposal`,
`confirm` and `reject` are the platform functions. A proposal is the only way
model output enters. Confirming is a person. The model does not compute a figure.
"""

from __future__ import annotations


def describe() -> dict:
    return {
        "module": "fracture.ai",
        "record": "fracture_guardrail.record_proposal",
        "confirm": "fracture_guardrail.confirm",
        "reject": "fracture_guardrail.reject",
        "kinds": [
            "field_mapping",
            "extraction",
            "classification",
            "narrative",
            "triage",
        ],
        "numeric_capable_kinds": ["extraction"],
        "rule": "Drafts, extracts and summarises. Does not compute a figure.",
    }


def record_proposal(*args, **kwargs):
    from fracture.ai import record_proposal as impl
    return impl(*args, **kwargs)


def confirm(*args, **kwargs):
    from fracture.ai import confirm as impl
    return impl(*args, **kwargs)


def reject(*args, **kwargs):
    from fracture.ai import reject as impl
    return impl(*args, **kwargs)
