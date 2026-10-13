"""Settings of a second heat source, read from its dependency item.

The options store the rule's parameter in percent and the two emitter types;
this module turns them into the split rule and the responses used by the model.
"""

from __future__ import annotations

from dataclasses import dataclass

from .dependencies import (
    KEY_LEAD_BAND,
    KEY_LEAD_EMITTER,
    KEY_LEAD_LAG_MIN,
    KEY_LEAD_TAU_MIN,
    KEY_SOURCE_ALONE,
    KEY_SOURCE_EMITTER,
    KEY_SOURCE_LAG_MIN,
    KEY_SOURCE_TAU_MIN,
    KEY_SPLIT_RATIO,
    KEY_SPLIT_RULE,
    DependencyItem,
)
from .source_model import EMITTER_RADIATOR, EMITTERS, SourceResponse
from .split import RULE_LEAD_BAND, RULES, SplitRule


def _percent(value, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default / 100.0
    return float(value) / 100.0


def _response(extra: dict, emitter_key: str, lag_key: str, tau_key: str) -> tuple[str, SourceResponse]:
    emitter = extra.get(emitter_key)
    if emitter not in EMITTERS:
        emitter = EMITTER_RADIATOR
    try:
        return emitter, SourceResponse.for_emitter(emitter, extra.get(lag_key), extra.get(tau_key))
    except (TypeError, ValueError):
        return EMITTER_RADIATOR, SourceResponse.for_emitter(EMITTER_RADIATOR)


@dataclass(frozen=True)
class SourceSettings:
    """The second source of a zone, as configured on the lead's entry."""

    item_id: str
    entity: str
    rule: SplitRule
    lead_emitter: str
    lead: SourceResponse
    source_emitter: str
    source: SourceResponse

    @classmethod
    def from_item(cls, item: DependencyItem) -> SourceSettings:
        """Settings of a source item; values that do not validate fall back to defaults."""
        extra = item.extra
        rule_name = extra.get(KEY_SPLIT_RULE)
        ratio = _percent(extra.get(KEY_SPLIT_RATIO), 100.0)
        rule = SplitRule(
            rule_name if rule_name in RULES else RULE_LEAD_BAND,
            ratio=ratio if ratio > 0.0 else 1.0,
            alone=min(max(_percent(extra.get(KEY_SOURCE_ALONE), 50.0), 0.0), 1.0),
            band=min(max(_percent(extra.get(KEY_LEAD_BAND), 20.0), 0.0), 1.0),
        )
        lead_emitter, lead = _response(extra, KEY_LEAD_EMITTER, KEY_LEAD_LAG_MIN, KEY_LEAD_TAU_MIN)
        source_emitter, source = _response(extra, KEY_SOURCE_EMITTER, KEY_SOURCE_LAG_MIN, KEY_SOURCE_TAU_MIN)
        return cls(item.item_id, item.entity, rule, lead_emitter, lead, source_emitter, source)
