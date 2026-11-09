"""Logger factory. Every logger the platform hands out is redacting."""

from __future__ import annotations

import logging
import os
import sys

from fracture.core.redaction import RedactingFilter

_CONFIGURED = False


def configure(level: str | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s %(message)s")
    )
    handler.addFilter(RedactingFilter())
    root = logging.getLogger("fracture")
    root.handlers = [handler]
    root.setLevel(level or os.environ.get("FRACTURE_LOG_LEVEL", "INFO"))
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger with the redaction filter attached.

    The filter is attached to the logger as well as the handler so that a caller
    who swaps in their own handler (pytest's caplog, Dagster's event capture)
    still gets redaction.
    """
    configure()
    logger = logging.getLogger(f"fracture.{name}" if not name.startswith("fracture") else name)
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(RedactingFilter())
    return logger
