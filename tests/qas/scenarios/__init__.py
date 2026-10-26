"""The QAS scenarios (tests/qas/README.md): importing this package registers them all in
``registry.SCENARIOS``."""

from . import qs  # noqa: F401 — each module registers its scenarios
from .registry import SCENARIOS, Scenario

__all__ = ["SCENARIOS", "Scenario"]
