"""Synthetic tenant generator (spec section 10)."""

from fracture.synth.config import DEMO_ESTATE, TEST_ESTATE, EstateSpec, FirmSpec
from fracture.synth.generator import EstateGenerator, GeneratedEstate, generate
from fracture.synth.load import LoadReport, load_estate

__all__ = [
    "EstateSpec", "FirmSpec", "DEMO_ESTATE", "TEST_ESTATE",
    "EstateGenerator", "GeneratedEstate", "generate",
    "load_estate", "LoadReport",
]
