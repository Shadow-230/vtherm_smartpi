"""Joint fit of a zone heated by two sources: losses, neighbours and both sources.

The zone follows

    dT/dt = a*u(t - L) - b*(T - T_ext) + a_f*x_f + sum_j k_j*(T_j - T)

with ``u`` the main source's power and ``x_f`` the additional source's heat
(both in power at the reference water when water data are used). Each
measurement window gives one row: the slope of the zone temperature, the window
means of ``u`` delayed by each candidate delay ``L``, of ``-(T - T_ext)``, of each
connected neighbour's ``T_j - T`` (zero while its door is closed) and of the
additional source's heat ``x_c`` for each candidate response ``c``. For each
pair ``(L, c)`` the rows are solved by least squares; the pair with the smallest
error gives the main source's delay and the additional source's time scale.

Nothing is taken from SmartPI's own model, so the fit works while SmartPI
still learns and its errors do not leak in. SmartPI's own dead time is not used
either: with slow rises and 0.1 degC sensors it comes out two to six times too
long, which shifted the main source's power in the windows and kept the fit
from publishing.

The fit is published once it holds ``ZONE_FIT_MIN_WINDOWS`` windows, its
columns are not collinear and ``a``, ``b`` and ``a_f`` each have a relative
standard error of at most ``ZONE_FIT_MAX_REL_SE``. The sums fade over
``ZONE_FIT_MEMORY_WINDOWS``. In cooling the caller gives the sources' columns
with a minus sign, so the fitted strengths stay positive.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from .const import (
    COUPLING_LS_COND_MAX,
    COUPLING_WINDOW_S,
    ZONE_FIT_MAX_GAP_S,
    ZONE_FIT_MAX_REL_SE,
    ZONE_FIT_MEMORY_WINDOWS,
    ZONE_FIT_MIN_WINDOWS,
    ZONE_FIT_SWITCH_GAIN,
)
from .coupling_estimator import _slope_weights

_BASE = 3  # a, b, a_f


@dataclass(frozen=True)
class ZoneFitResult:
    a: float
    b: float
    a_f: float
    a_f_se: float
    # Index of the additional source's response among its candidates.
    candidate: int
    windows: float
    k: tuple[tuple[str, float], ...] = ()
    # The main source's delay chosen by the fit.
    lead_delay_s: float = 0.0
    # Standard error of each neighbour's k.
    k_se: tuple[tuple[str, float], ...] = ()


def _compact(value: float) -> float:
    return float(f"{value:.10g}")


def _solve(m: list[list[float]], v: list[float]) -> list[float] | None:
    """Solve a square system by Gaussian elimination with partial pivoting."""
    n = len(v)
    a = [row[:] + [v[i]] for i, row in enumerate(m)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-18:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(col + 1, n):
            f = a[r][col] / a[col][col]
            for c in range(col, n + 1):
                a[r][c] -= f * a[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (a[r][n] - sum(a[r][c] * x[c] for c in range(r + 1, n))) / a[r][r]
    return x


def _inverse(m: list[list[float]]) -> list[list[float]] | None:
    n = len(m)
    columns = []
    for i in range(n):
        col = _solve(m, [1.0 if j == i else 0.0 for j in range(n)])
        if col is None:
            return None
        columns.append(col)
    return [[columns[j][i] for j in range(n)] for i in range(n)]


def _condition(m: list[list[float]]) -> float:
    """Condition number of the normalized matrix (1-norm)."""
    n = len(m)
    d = [math.sqrt(m[i][i]) if m[i][i] > 0.0 else 0.0 for i in range(n)]
    if any(value <= 0.0 for value in d):
        return math.inf
    norm_m = [[m[i][j] / (d[i] * d[j]) for j in range(n)] for i in range(n)]
    inv = _inverse(norm_m)
    if inv is None:
        return math.inf
    norm = max(sum(abs(norm_m[i][j]) for i in range(n)) for j in range(n))
    norm_inv = max(sum(abs(inv[i][j]) for i in range(n)) for j in range(n))
    return norm * norm_inv


class ZoneFit:
    """Least-squares fit of the zone for each candidate delay and response."""

    def __init__(self, candidates: int, start_index: int, delays_s: tuple[float, ...]) -> None:
        # Joint candidates: index = delay_index * candidates + response_index.
        self._sources = candidates
        self._delays = tuple(float(d) for d in delays_s)
        self._count = candidates * len(self._delays)
        self._start_index = start_index
        self.current: int | None = None
        self.result: ZoneFitResult | None = None
        self._neighbours: tuple[str, ...] = ()
        self._samples: list = []
        self._reset_sums()

    def _reset_sums(self) -> None:
        size = _BASE + len(self._neighbours)
        self._xtx = [[[0.0] * size for _ in range(size)] for _ in range(self._count)]
        self._xty = [[0.0] * size for _ in range(self._count)]
        self._yty = 0.0
        self._n = 0.0

    @property
    def windows(self) -> float:
        return self._n

    def reset_window(self) -> None:
        self._samples.clear()

    def set_neighbours(self, neighbours: tuple[str, ...]) -> None:
        """The neighbours the zone may exchange with; a new set starts the sums over."""
        neighbours = tuple(sorted(neighbours))
        if neighbours != self._neighbours:
            self._neighbours = neighbours
            self._samples.clear()
            self._reset_sums()

    def add_sample(
        self,
        t_s: float,
        zone_temp: float,
        ext_temp: float,
        x_values: tuple[float, ...],
        delayed_power: Callable[[float, float, float], float],
        exchanges: dict[str, float] | None = None,
    ) -> bool:
        """Add one calculation; return True when a window closed and the fit changed.

        ``x_values`` holds the additional source's heat for each candidate;
        ``delayed_power(start_s, end_s, delay_s)`` the main source's mean power;
        ``exchanges`` maps each neighbour to ``T_j - T`` while it is connected.
        """
        if len(x_values) != self._sources:
            return False
        if not all(math.isfinite(v) for v in (t_s, zone_temp, ext_temp, *x_values)):
            return False
        if self._samples and t_s <= self._samples[-1][0]:
            return False
        if self._samples and t_s - self._samples[-1][0] > ZONE_FIT_MAX_GAP_S:
            # A gap between calculations: no window spans it.
            self._samples.clear()
        exchanges = exchanges or {}
        gaps = tuple(exchanges.get(item_id, 0.0) for item_id in self._neighbours)
        self._samples.append((t_s, zone_temp, ext_temp, x_values, gaps))
        if self._samples[-1][0] - self._samples[0][0] < COUPLING_WINDOW_S:
            return False
        samples, self._samples = self._samples, [self._samples[-1]]
        return self._add_window(samples, delayed_power)

    def _add_window(self, samples, delayed_power) -> bool:
        weights = _slope_weights([s[0] for s in samples])
        if weights is None:
            return False
        slope = gap = 0.0
        us = [0.0] * len(self._delays)
        xs = [0.0] * self._sources
        ns = [0.0] * len(self._neighbours)
        for w, first, second in zip(weights, samples, samples[1:]):
            dt = second[0] - first[0]
            slope += w * (second[1] - first[1]) / dt * 60.0
            for d, delay in enumerate(self._delays):
                us[d] += w * delayed_power(first[0], second[0], delay)
            gap += w * 0.5 * (first[1] - first[2] + second[1] - second[2])
            for i in range(self._sources):
                xs[i] += w * 0.5 * (first[3][i] + second[3][i])
            for j in range(len(ns)):
                ns[j] += w * 0.5 * (first[4][j] + second[4][j])
        if not all(math.isfinite(v) for v in (slope, gap, *us, *xs, *ns)):
            return False
        y = slope
        fade = 1.0 - 1.0 / ZONE_FIT_MEMORY_WINDOWS
        self._yty = fade * self._yty + y * y
        self._n = fade * self._n + 1.0
        for c in range(self._count):
            row = (us[c // self._sources], -gap, xs[c % self._sources], *ns)
            size = len(row)
            for i in range(size):
                self._xty[c][i] = fade * self._xty[c][i] + row[i] * y
                for j in range(size):
                    self._xtx[c][i][j] = fade * self._xtx[c][i][j] + row[i] * row[j]
        return self._fit()

    def _active(self, c: int) -> list[int]:
        """Columns with data: a neighbour never connected is left out of the fit."""
        return [i for i in range(len(self._xty[c])) if i < _BASE or self._xtx[c][i][i] > 0.0]

    def _solve_candidate(self, c: int):
        cols = self._active(c)
        m = [[self._xtx[c][i][j] for j in cols] for i in cols]
        v = [self._xty[c][i] for i in cols]
        theta = _solve(m, v)
        if theta is None:
            return None
        sse = self._yty - 2.0 * sum(t * vi for t, vi in zip(theta, v)) + sum(
            theta[i] * m[i][j] * theta[j] for i in range(len(cols)) for j in range(len(cols))
        )
        return cols, m, theta, max(sse, 0.0)

    def _fit(self) -> bool:
        if self._n < ZONE_FIT_MIN_WINDOWS:
            return False
        fits = []
        for c in range(self._count):
            solved = self._solve_candidate(c)
            if solved is None:
                continue
            cols, m, theta, sse = solved
            if min(theta[:_BASE]) <= 0.0:
                continue
            fits.append((sse, c, cols, m, theta))
        if not fits:
            return False
        best = min(fits, key=lambda f: f[0])
        current = next((f for f in fits if f[1] == self.current), None)
        if current is not None and best[1] != self.current and best[0] > (1.0 - ZONE_FIT_SWITCH_GAIN) * current[0]:
            best = current
        sse, c, cols, m, theta = best
        if _condition(m) > COUPLING_LS_COND_MAX * 10.0:
            return False
        inv = _inverse(m)
        if inv is None:
            return False
        dof = max(self._n - len(cols), 1.0)
        errors = [math.sqrt(max(sse / dof * inv[i][i], 0.0)) for i in range(len(cols))]
        if any(errors[i] > ZONE_FIT_MAX_REL_SE * theta[i] for i in range(_BASE)):
            return False
        k = tuple(
            (self._neighbours[col - _BASE], theta[index]) for index, col in enumerate(cols) if col >= _BASE
        )
        k_se = tuple(
            (self._neighbours[col - _BASE], errors[index]) for index, col in enumerate(cols) if col >= _BASE
        )
        self.current = c
        self.result = ZoneFitResult(
            theta[0], theta[1], theta[2], errors[2], c % self._sources, self._n, k, self._delays[c // self._sources], k_se
        )
        return True

    # --- persistence -------------------------------------------------------

    def save_state(self) -> dict | None:
        """The sums and the result (None before the first window: nothing to keep).

        Sums are kept to 10 significant digits, which the least squares do not
        need more of, to keep the saved state small.
        """
        r = self.result
        if self._n <= 0.0 and r is None:
            return None
        return {
            "n": _compact(self._n),
            "yty": _compact(self._yty),
            "xtx": [[[_compact(v) for v in row] for row in m] for m in self._xtx],
            "xty": [[_compact(v) for v in row] for row in self._xty],
            "current": self.current,
            "neighbours": list(self._neighbours),
            "delays_s": list(self._delays),
            "result": None
            if r is None
            else [
                r.a,
                r.b,
                r.a_f,
                r.a_f_se,
                r.candidate,
                r.windows,
                [list(p) for p in r.k],
                r.lead_delay_s,
                [list(p) for p in r.k_se],
            ],
        }

    def load_state(self, state) -> None:
        """Resume the sums and the result; a state of another shape is dropped."""
        if not isinstance(state, dict):
            return
        try:
            xtx, xty = state["xtx"], state["xty"]
            neighbours = tuple(str(n) for n in state.get("neighbours", ()))
            delays = tuple(float(d) for d in state.get("delays_s", ()))
            if delays != self._delays or len(xtx) != self._count or len(xty) != self._count:
                return
            if any(len(row) != _BASE + len(neighbours) for row in xty):
                return
            loaded_xtx = [[[float(v) for v in row] for row in m] for m in xtx]
            loaded_xty = [[float(v) for v in row] for row in xty]
            n, yty = float(state["n"]), float(state["yty"])
            current = None if state["current"] is None else int(state["current"])
            r = state.get("result")
            result = (
                None
                if r is None
                else ZoneFitResult(
                    float(r[0]),
                    float(r[1]),
                    float(r[2]),
                    float(r[3]),
                    int(r[4]),
                    float(r[5]),
                    tuple((str(p[0]), float(p[1])) for p in r[6]),
                    float(r[7]),
                    tuple((str(p[0]), float(p[1])) for p in r[8]) if len(r) > 8 else (),
                )
            )
        except (KeyError, TypeError, ValueError, IndexError):
            return
        if not all(math.isfinite(v) for v in (n, yty)):
            return
        self._neighbours = neighbours
        self._xtx, self._xty = loaded_xtx, loaded_xty
        self._n, self._yty = n, yty
        self.current = current
        self.result = result
