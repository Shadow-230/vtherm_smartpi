"""Learning of the heat exchange between the zone and its neighbours.

With neighbours ``j`` connected, the zone follows

    dT/dt = a*u(t - L) - b*(T - T_ext) - sum_j k_j*(T - T_j) + q

Once SmartPI's own model (a, b, dead time L) is reliable, the exchange is the
residual of a measurement window: ``r = dT/dt - (a*u - b*(T - T_ext) + q)``
with ``r = sum_j k_j * x_j`` and ``x_j = -(T - T_j)`` averaged over the window.

- One connected neighbour behind a door: ``k = r / x``, accepted when the
  temperature difference carries information; the published value is the
  median of the accepted history once it holds ``COUPLING_MIN_SAMPLES`` values.
  SmartPI's b is clean here, since it learns only while the doors are closed.
- Several door connections: windows feed a shared buffer solved by least
  squares with ``k >= 0``.
- An always-connected neighbour: SmartPI's b has absorbed the exchange, so b
  is fitted together with the k values (``slope - a*u - q = b*x_ext + sum k_j*x_j``
  with ``x_ext = -(T - T_ext)``).

The joint fit of always-connected neighbours starts once the buffer holds
``COUPLING_JOINT_MIN_ROWS`` windows. Written as
``r = (b + sum k_j)*x_ext + sum k_j*(T_j - T_ext)``, each k is told apart only
by the neighbour's own temperature moving independently of the outdoor one
and of the zone, so b and the k values are separable when the centered
columns ``x_ext`` and ``T_j - T_ext`` are not collinear (condition number of
their normalized Gram matrix at most ``COUPLING_LS_COND_MAX``; a constant
offset alone would let a constant error, such as unmodelled gains, decide the
split) and each fitted coefficient is positive with a standard error of at
most ``COUPLING_JOINT_MAX_REL_SE`` of its value. A separable fit adds k (and the b
fitted with it) to a history every ``COUPLING_JOINT_FIT_SPACING`` new windows;
like single measurements, a history is published as its median once it holds
``COUPLING_MIN_SAMPLES`` values that agree (MAD over median at most
``COUPLING_MAX_DISPERSION``). Otherwise k stays unknown and the exchange
remains in b as today.
Door connections open together whose regressors cannot be told apart
(condition number of the normalized Gram matrix above ``COUPLING_LS_COND_MAX``)
get one common coefficient (their total exchange is preserved) and are
flagged as indistinguishable.

Windows have a fixed length, longer than SmartPI's own learning windows
because a 0.1 degC sensor needs time to show a slope. Their slope is a plain
least-squares fit: a steady temperature is informative too, since the applied
power then balances the losses. That slope is a weighted mean of the
temperature derivative, heavier in the middle of the window, so the power and
the temperature differences are averaged with the same weights: the window
model then holds even when the temperature curves, for example with the ripple
of on/off cycles.

The power acting on the zone is the applied power shifted by the dead time:
the power realized by each completed cycle, as Versatile Thermostat reports
it, delivered from the start of the cycle by a switched actuator, and the
commanded power where no cycle has completed yet. A cycle cut short delivers
more than its commanded average, since it starts with its on-time.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Mapping

from .ab_drift import robust_mad, robust_median
from .const import (
    AB_MAD_K,
    COUPLING_HISTORY_SIZE,
    COUPLING_K_MAX,
    COUPLING_JOINT_FIT_SPACING,
    COUPLING_JOINT_MAX_REL_SE,
    COUPLING_JOINT_MIN_ROWS,
    COUPLING_JOINT_WINDOW,
    COUPLING_K_MIN_DT,
    COUPLING_LS_COND_MAX,
    COUPLING_LS_WINDOW,
    COUPLING_MAX_DISPERSION,
    COUPLING_MIN_SAMPLES,
    COUPLING_WINDOW_S,
)

OUTLIER_MIN_HISTORY = 5
OUTLIER_MAD_FACTOR = 3.0
MIN_WINDOW_SAMPLES = 10


def _slope_weights(times: list[float]) -> list[float] | None:
    """Weights of the intervals between samples in their least-squares slope.

    The least-squares slope of values sampled at ``times`` equals the mean of
    the interval derivatives weighted by these values, which sum to 1.
    """
    n = len(times)
    if n < MIN_WINDOW_SAMPLES:
        return None
    mean_t = sum(times) / n
    ss_tt = sum((t - mean_t) ** 2 for t in times)
    if ss_tt <= 0.0:
        return None
    weights = [0.0] * (n - 1)
    later = 0.0
    for i in range(n - 2, -1, -1):
        later += (times[i + 1] - mean_t) / ss_tt
        weights[i] = later * (times[i + 1] - times[i])
    return weights


@dataclass(frozen=True)
class CouplingTick:
    """Zone state at one calculation, as seen by the coupling estimator."""

    now_s: float
    zone_temp: float
    ext_temp: float
    power: float
    a: float
    b: float
    deadtime_s: float
    # Usable connected neighbours: item id -> neighbour temperature.
    connected: Mapping[str, float]
    # False while learning is gated or SmartPI's model is not reliable.
    learning_allowed: bool
    # Connected neighbours that are always connected (b is fitted with them).
    always: frozenset[str] = frozenset()
    # Direct heat from other known inputs (degC/min).
    known_heat: float = 0.0


@dataclass(frozen=True)
class _Sample:
    t_s: float
    zone_temp: float
    ext_temp: float
    # Neighbour temperatures, in the order of the window's ids.
    neighbours: tuple[float, ...]
    heat: float


@dataclass
class _Window:
    ids: tuple[str, ...]
    samples: list[_Sample] = field(default_factory=list)

    @property
    def span_s(self) -> float:
        return self.samples[-1].t_s - self.samples[0].t_s


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """Solve a small linear system by Gaussian elimination with partial pivoting."""
    n = len(vector)
    rows = [list(matrix[i]) + [vector[i]] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(rows[r][col]))
        if abs(rows[pivot][col]) < 1e-15:
            return None
        rows[col], rows[pivot] = rows[pivot], rows[col]
        for r in range(n):
            if r != col:
                factor = rows[r][col] / rows[col][col]
                rows[r] = [v - factor * w for v, w in zip(rows[r], rows[col])]
    return [rows[i][n] / rows[i][i] for i in range(n)]


def _symmetric_eigenvalues(matrix: list[list[float]]) -> list[float]:
    """Eigenvalues of a small symmetric matrix (cyclic Jacobi rotations)."""
    n = len(matrix)
    a = [list(row) for row in matrix]
    for _sweep in range(50):
        off = sum(a[i][j] ** 2 for i in range(n) for j in range(n) if i != j)
        if off < 1e-24:
            break
        for p in range(n):
            for q in range(p + 1, n):
                if abs(a[p][q]) < 1e-30:
                    continue
                theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
                t = math.copysign(1.0, theta) / (abs(theta) + math.sqrt(theta * theta + 1.0))
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                for k in range(n):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p], a[k][q] = c * akp - s * akq, s * akp + c * akq
                for k in range(n):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k], a[q][k] = c * apk - s * aqk, s * apk + c * aqk
    return [a[i][i] for i in range(n)]


# Regressor of the zone-to-outdoor difference in the joint rows (b is its coefficient).
_EXT = "_ext"


def _boundary_rows(
    rows: list[tuple[float, Mapping[str, float]]],
) -> list[tuple[float, dict[str, float]]]:
    """Rows with each neighbour's regressor replaced by ``T_j - T_ext``."""
    return [
        (r, {i: (v if i == _EXT else v - x.get(_EXT, 0.0)) for i, v in x.items()})
        for r, x in rows
    ]


def _least_squares_with_errors(
    rows: list[tuple[float, Mapping[str, float]]], ids: list[str]
) -> tuple[list[float], list[float]] | None:
    """Least squares of r = sum c_j x_j, with the standard error of each coefficient."""
    count, size = len(rows), len(ids)
    if count <= size:
        return None
    gram = [[sum(x.get(i, 0.0) * x.get(j, 0.0) for _r, x in rows) for j in ids] for i in ids]
    solution = _solve(gram, [sum(r * x.get(i, 0.0) for r, x in rows) for i in ids])
    if solution is None:
        return None
    residuals = (r - sum(c * x.get(i, 0.0) for c, i in zip(solution, ids)) for r, x in rows)
    variance = sum(value * value for value in residuals) / (count - size)
    errors = []
    for column in range(size):
        inverse = _solve(gram, [1.0 if row == column else 0.0 for row in range(size)])
        if inverse is None:
            return None
        errors.append(math.sqrt(max(variance * inverse[column], 0.0)))
    return solution, errors


def _nonnegative_least_squares(
    rows: list[tuple[float, Mapping[str, float]]], ids: list[str]
) -> dict[str, float] | None:
    """Least squares of r = sum k_j x_j with k_j >= 0 (active-set elimination)."""
    free = list(ids)
    while free:
        matrix = [[sum(x.get(i, 0.0) * x.get(j, 0.0) for _r, x in rows) for j in free] for i in free]
        vector = [sum(r * x.get(i, 0.0) for r, x in rows) for i in free]
        solution = _solve(matrix, vector)
        if solution is None:
            return None
        negative = [(value, name) for value, name in zip(solution, free) if value < 0.0]
        if not negative:
            result = {name: 0.0 for name in ids}
            result.update(dict(zip(free, solution)))
            return result
        free.remove(min(negative)[1])
    return {name: 0.0 for name in ids}


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _floats(values: Any) -> list[float]:
    if not isinstance(values, (list, tuple)):
        return []
    return [float(v) for v in values if _number(v)]


def _row(value: Any) -> tuple[float, dict[str, float]] | None:
    """A saved least-squares row, or None when it cannot be used."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    residual, regressors = value
    if not _number(residual) or not isinstance(regressors, dict) or not regressors:
        return None
    if not all(_number(v) for v in regressors.values()):
        return None
    return float(residual), {str(k): float(v) for k, v in regressors.items()}


def _agreed_median(values) -> float | None:
    """Median of enough values that agree with each other, else None."""
    if values is None or len(values) < COUPLING_MIN_SAMPLES:
        return None
    data = list(values)
    median = robust_median(data)
    mad = robust_mad(data, median)
    if median is None or median <= 0.0 or mad is None or mad / median > COUPLING_MAX_DISPERSION:
        return None
    return median


def _well_conditioned(
    rows: list[tuple[float, Mapping[str, float]]], ids: list[str], centered: bool = False
) -> bool:
    """Whether the regressors can be told apart (normalized Gram matrix condition number).

    ``centered`` compares their variations around their means only, each of
    which must reach ``COUPLING_K_MIN_DT`` (standard deviation).
    """
    means = {i: 0.0 for i in ids}
    if centered and rows:
        means = {i: sum(x.get(i, 0.0) for _r, x in rows) / len(rows) for i in ids}
    columns = {i: [x.get(i, 0.0) - means[i] for _r, x in rows] for i in ids}
    gram = [[sum(a * b for a, b in zip(columns[i], columns[j])) for j in ids] for i in ids]
    scale = [math.sqrt(gram[i][i]) for i in range(len(ids))]
    if any(value <= 0.0 for value in scale):
        return False
    if centered and any(value / math.sqrt(len(rows)) < COUPLING_K_MIN_DT for value in scale):
        return False
    normalized = [
        [gram[i][j] / (scale[i] * scale[j]) for j in range(len(ids))] for i in range(len(ids))
    ]
    eigen = _symmetric_eigenvalues(normalized)
    smallest, largest = min(eigen), max(eigen)
    return smallest > 0.0 and largest / smallest <= COUPLING_LS_COND_MAX


class CouplingEstimator:
    """Exchange coefficients k of the zone's neighbours, learned from measurement windows."""

    def __init__(self) -> None:
        self._history: dict[str, Deque[float]] = {}
        self._ls_rows: Deque[tuple[float, dict[str, float]]] = deque(maxlen=COUPLING_LS_WINDOW)
        # Rows with b as an unknown: residual without b, regressors include "ext".
        self._joint_rows: Deque[tuple[float, dict[str, float]]] = deque(maxlen=COUPLING_JOINT_WINDOW)
        self._ls_k: dict[str, float] = {}
        # Fits of always-connected neighbours from separable buffers, and the
        # loss coefficients fitted with them.
        self._joint_k: dict[str, Deque[float]] = {}
        self._joint_b: Deque[float] = deque(maxlen=COUPLING_HISTORY_SIZE)
        # Joint windows added since the last fit entered the history.
        self._joint_rows_since_fit = 0
        # Door connections sharing one common coefficient.
        self._indistinguishable: set[str] = set()
        # Always-connected neighbours the current buffer cannot separate from b.
        self._joint_inseparable: set[str] = set()
        self._window: _Window | None = None
        self._power: Deque[tuple[float, float]] = deque()
        # Completed cycles: (start, end, realized power).
        self._realized: Deque[tuple[float, float, float]] = deque()

    # --- published values -------------------------------------------------

    def k(self, item_id: str) -> float | None:
        """Return the reliable exchange coefficient of a neighbour, or None."""
        published = _agreed_median(self._history.get(item_id))
        if published is not None:
            return published
        if item_id in self._ls_k:
            return self._ls_k[item_id]
        return _agreed_median(self._joint_k.get(item_id))

    def loss_coefficient(self) -> float | None:
        """Loss coefficient fitted together with always-connected neighbours, once agreed."""
        if not any(_agreed_median(history) is not None for history in self._joint_k.values()):
            return None
        return _agreed_median(self._joint_b)

    def samples(self, item_id: str) -> int:
        single = len(self._history.get(item_id, ()))
        shared = sum(1 for _r, x in (*self._ls_rows, *self._joint_rows) if item_id in x)
        return single + shared

    def is_indistinguishable(self, item_id: str) -> bool:
        """True when a common coefficient is used, or when nothing could be separated."""
        if item_id in self._indistinguishable:
            return True
        return item_id in self._joint_inseparable and self.k(item_id) is None

    # --- persistence ------------------------------------------------------

    def save_item(self, item_id: str) -> dict[str, Any]:
        return {"k_history": list(self._history.get(item_id, ()))}

    def load_item(self, item_id: str, state: Mapping[str, Any]) -> None:
        values = _floats(state.get("k_history"))
        if values:
            self._history[item_id] = deque(values, maxlen=COUPLING_HISTORY_SIZE)

    def save_shared(self) -> dict[str, Any]:
        return {
            "ls_rows": [[r, dict(x)] for r, x in self._ls_rows],
            "joint_rows": [[r, dict(x)] for r, x in self._joint_rows],
            "joint_k": {item_id: list(values) for item_id, values in self._joint_k.items()},
            "joint_b": list(self._joint_b),
        }

    def load_shared(self, state: Mapping[str, Any]) -> None:
        for key, rows in (("ls_rows", self._ls_rows), ("joint_rows", self._joint_rows)):
            rows.clear()
            saved = state.get(key)
            rows.extend(row for row in map(_row, saved if isinstance(saved, list) else []) if row)
        self._joint_b = deque(_floats(state.get("joint_b")), maxlen=COUPLING_HISTORY_SIZE)
        joint_k = state.get("joint_k", {})
        self._joint_k = {
            str(item_id): deque(_floats(values), maxlen=COUPLING_HISTORY_SIZE)
            for item_id, values in (joint_k.items() if isinstance(joint_k, dict) else ())
        }
        self._solve_shared(fit_joint=False)

    def forget_except(self, item_ids: set[str]) -> None:
        """Drop everything learned about neighbours that are no longer configured."""
        for item_id in list(self._history):
            if item_id not in item_ids:
                del self._history[item_id]
        for item_id in list(self._joint_k):
            if item_id not in item_ids:
                del self._joint_k[item_id]
        for rows, allowed in ((self._ls_rows, item_ids), (self._joint_rows, item_ids | {_EXT})):
            kept = [(r, x) for r, x in rows if set(x) <= allowed]
            rows.clear()
            rows.extend(kept)
        self._solve_shared(fit_joint=False)

    def reset(self) -> None:
        self._history.clear()
        self._ls_rows.clear()
        self._joint_rows.clear()
        self._ls_k.clear()
        self._joint_k.clear()
        self._joint_b.clear()
        self._joint_inseparable.clear()
        self._indistinguishable.clear()
        self._joint_rows_since_fit = 0
        self._window = None

    # --- learning ---------------------------------------------------------

    def update(self, tick: CouplingTick) -> bool:
        """Feed one calculation; return True when a new value was learned."""
        self._record_power(tick)
        ids = tuple(sorted(tick.connected)) if tick.learning_allowed else ()
        window = self._window
        if not ids:
            self._window = None
            return False
        if window is None or window.ids != ids:
            self._window = self._new_window(ids, tick)
            return False

        if tick.now_s <= window.samples[-1].t_s:
            return False
        window.samples.append(self._sample(ids, tick))

        if window.span_s < COUPLING_WINDOW_S:
            return False
        learned = self._submit(window, tick)
        self._window = self._new_window(ids, tick)
        return learned

    @staticmethod
    def _sample(ids: tuple[str, ...], tick: CouplingTick) -> _Sample:
        return _Sample(
            t_s=tick.now_s,
            zone_temp=tick.zone_temp,
            ext_temp=tick.ext_temp,
            neighbours=tuple(tick.connected[item_id] for item_id in ids),
            heat=tick.known_heat,
        )

    def _new_window(self, ids: tuple[str, ...], tick: CouplingTick) -> _Window:
        return _Window(ids=ids, samples=[self._sample(ids, tick)])

    def record_realized_power(
        self, start_s: float, end_s: float, power: float, switched: bool = False
    ) -> None:
        """Record the average power a completed cycle delivered over [start_s, end_s].

        A switched actuator delivers it at full power from the start of the cycle.
        """
        if end_s <= start_s or not math.isfinite(power):
            return
        power = min(max(power, 0.0), 1.0)
        if not switched:
            self._realized.append((start_s, end_s, power))
            return
        on_end_s = start_s + power * (end_s - start_s)
        if on_end_s > start_s:
            self._realized.append((start_s, on_end_s, 1.0))
        if end_s > on_end_s:
            self._realized.append((on_end_s, end_s, 0.0))

    def _record_power(self, tick: CouplingTick) -> None:
        self._power.append((tick.now_s, tick.power))
        horizon = tick.now_s - (tick.deadtime_s + COUPLING_WINDOW_S + 3600.0)
        while len(self._power) > 2 and self._power[1][0] <= horizon:
            self._power.popleft()
        while self._realized and self._realized[0][1] <= horizon:
            self._realized.popleft()

    def _delayed_power(self, start_s: float, end_s: float) -> float:
        """Average power over [start_s, end_s]: realized by cycles, else commanded."""
        span = end_s - start_s
        if span <= 0.0:
            return 0.0
        total = self._commanded_energy(start_s, end_s)
        for seg_start, seg_end, power in self._realized:
            low, high = max(seg_start, start_s), min(seg_end, end_s)
            if high > low:
                total += power * (high - low) - self._commanded_energy(low, high)
        return total / span

    def _commanded_energy(self, start_s: float, end_s: float) -> float:
        """Integral of the commanded power over [start_s, end_s] (constant between ticks)."""
        total = 0.0
        points = list(self._power)
        for index, (t_s, value) in enumerate(points):
            seg_start = max(t_s, start_s)
            seg_end = points[index + 1][0] if index + 1 < len(points) else end_s
            seg_end = min(seg_end, end_s)
            if seg_end > seg_start:
                total += value * (seg_end - seg_start)
        first_t = points[0][0] if points else end_s
        if first_t > start_s:
            total += (points[0][1] if points else 0.0) * (min(first_t, end_s) - start_s)
        return total

    def _submit(self, window: _Window, tick: CouplingTick) -> bool:
        samples = window.samples
        weights = _slope_weights([sample.t_s for sample in samples])
        if weights is None:
            return False
        delay = max(tick.deadtime_s, 0.0)
        slope = u = heat = gap_ext = 0.0
        gaps = [0.0] * len(window.ids)
        for weight, first, second in zip(weights, samples, samples[1:]):
            dt = second.t_s - first.t_s
            slope += weight * (second.zone_temp - first.zone_temp) / dt * 60.0
            u += weight * self._delayed_power(first.t_s - delay, second.t_s - delay)
            heat += weight * 0.5 * (first.heat + second.heat)
            gap_ext += weight * 0.5 * (
                first.zone_temp - first.ext_temp + second.zone_temp - second.ext_temp
            )
            for index in range(len(gaps)):
                gaps[index] += weight * 0.5 * (
                    first.zone_temp - first.neighbours[index]
                    + second.zone_temp - second.neighbours[index]
                )
        x = {item_id: -gap for item_id, gap in zip(window.ids, gaps)}
        if all(abs(value) < COUPLING_K_MIN_DT for value in x.values()):
            return False
        known = tick.a * u + heat
        if tick.always & set(x):
            x[_EXT] = -gap_ext
            self._joint_rows.append((slope - known, x))
            self._joint_rows_since_fit += 1
            self._solve_shared()
            return True
        residual = slope - (known - tick.b * gap_ext)
        if len(x) == 1:
            ((item_id, value),) = x.items()
            return self._add_single(item_id, residual / value)
        self._ls_rows.append((residual, x))
        self._solve_shared()
        return True

    def _add_single(self, item_id: str, k_meas: float) -> bool:
        if not 0.0 < k_meas <= COUPLING_K_MAX:
            return False
        history = self._history.setdefault(item_id, deque(maxlen=COUPLING_HISTORY_SIZE))
        if len(history) >= OUTLIER_MIN_HISTORY:
            values = list(history)
            median = robust_median(values)
            mad = robust_mad(values, median)
            if median is not None and mad is not None and mad > 0.0:
                if abs(k_meas - median) > OUTLIER_MAD_FACTOR * AB_MAD_K * mad:
                    return False
        history.append(k_meas)
        return True

    def _solve_shared(self, fit_joint: bool = True) -> None:
        """Recompute the door fit; with ``fit_joint``, add a fit of the always buffer."""
        self._ls_k.clear()
        self._indistinguishable.clear()
        self._solve_door_rows(list(self._ls_rows))
        if fit_joint:
            self._joint_inseparable.clear()
            self._solve_joint_rows(list(self._joint_rows))

    def _solve_joint_rows(self, rows: list[tuple[float, dict[str, float]]]) -> None:
        """Fit b with the always-connected neighbours; keep the fit only when separable."""
        if len(rows) < COUPLING_JOINT_MIN_ROWS:
            return
        ids = sorted({item_id for _r, x in rows for item_id in x})
        separable = _well_conditioned(_boundary_rows(rows), ids, centered=True)
        fit = _least_squares_with_errors(rows, ids) if separable else None
        if fit is None or not all(
            value > 0.0 and error <= COUPLING_JOINT_MAX_REL_SE * value
            for value, error in zip(*fit)
        ):
            self._joint_inseparable.update(item_id for item_id in ids if item_id != _EXT)
            return
        if self._joint_k and self._joint_rows_since_fit < COUPLING_JOINT_FIT_SPACING:
            return
        solution = dict(zip(ids, fit[0]))
        if any(value > COUPLING_K_MAX for item_id, value in solution.items() if item_id != _EXT):
            return
        for item_id, value in solution.items():
            if item_id != _EXT:
                self._joint_k.setdefault(item_id, deque(maxlen=COUPLING_HISTORY_SIZE)).append(value)
        self._joint_b.append(solution[_EXT])
        self._joint_rows_since_fit = 0

    def _solve_door_rows(self, rows: list[tuple[float, dict[str, float]]]) -> None:
        """Fit the k values of door connections that were open together."""
        if len(rows) < COUPLING_MIN_SAMPLES:
            return
        ids = sorted({item_id for _r, x in rows for item_id in x})
        if not _well_conditioned(rows, ids):
            # One coefficient shared by all connections: r = k * sum_j x_j.
            sums = [sum(x.values()) for _r, x in rows]
            norm = sum(s * s for s in sums)
            if norm <= 0.0:
                return
            shared = sum(r * s for (r, _x), s in zip(rows, sums)) / norm
            if not 0.0 < shared <= COUPLING_K_MAX:
                return
            for item_id in ids:
                self._ls_k[item_id] = shared
                self._indistinguishable.add(item_id)
            return
        solution = _nonnegative_least_squares(rows, ids)
        if solution is None:
            return
        for item_id, value in solution.items():
            if 0.0 < value <= COUPLING_K_MAX:
                self._ls_k[item_id] = value
