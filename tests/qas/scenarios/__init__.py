"""The QAS scenarios (documentation/en/testing.md, the QAS section): importing this package
registers them all in ``registry.SCENARIOS``."""

from . import qa, qb, qc, qd, qe, qz  # noqa: F401 — each module registers its scenarios
from .registry import PLANNED, SCENARIOS, Planned, Scenario

__all__ = ["PLANNED", "SCENARIOS", "Planned", "Scenario"]
