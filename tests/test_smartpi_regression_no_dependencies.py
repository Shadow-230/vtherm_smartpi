"""Without active model inputs SmartPI behaves exactly as without the dependency layer.

The same simulation runs twice: once with configured dependencies that provide
no active model input, once with the substitution point switched off. Traces
and the saved state must match bit for bit. Nothing is recorded in advance, so
the comparison follows any change of the algorithm itself.
"""

from __future__ import annotations

from custom_components.vtherm_smartpi.smartpi.effective_model import KnownInputs

from sim.runner import Simulation, ZoneSpec
from sim.weather import Weather

STATUS = ({"id": "n1", "kind": "neighbour", "entity": "climate.hall", "active": False},)


async def _run(monkeypatch, substitution_enabled: bool):
    sim = Simulation(
        monkeypatch,
        ZoneSpec(),
        Weather(base_c=3.0, daily_amplitude_c=4.0),
        known_inputs=lambda _sim: KnownInputs(status=STATUS),
    )
    sim.algo._known_inputs_enabled = substitution_enabled
    await sim.run_for(36.0)
    return sim.trace, sim.algo.save_state()


async def test_inactive_dependencies_change_nothing(monkeypatch) -> None:
    with monkeypatch.context() as patch:
        configured = await _run(patch, substitution_enabled=True)
    with monkeypatch.context() as patch:
        disabled = await _run(patch, substitution_enabled=False)

    assert configured[0] == disabled[0]
    assert configured[1] == disabled[1]
