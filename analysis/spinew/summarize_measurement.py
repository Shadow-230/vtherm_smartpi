"""Summarize res_dev.json and res_governor.json per variant (12 approaches each)."""
import json
import sys
from collections import Counter, defaultdict
from statistics import mean

base = sys.argv[1]
rows = defaultdict(lambda: defaultdict(list))
for label in ("dev", "governor"):
    for r in json.load(open(f"{base}/res_{label}.json")):
        for a in r["approaches"]:
            rows[r["variant"]][label].append((a, r["water_factor"], r["a_ratio"]))

print(f"{'wariant':22} {'wersja':9} {'wsp.wody':>8} {'a/prawda':>8} {'dojście h':>10} {'przekr. °C śr/max':>18} "
      f"{'cap min':>8} {'przełącz.':>9} {'woda ogr. min':>13} {'uzbr.':>6} {'wycofania':>10}")
for variant, by in rows.items():
    for label in ("dev", "governor"):
        items = by[label]
        if not items:
            continue
        aps = [a for a, *_ in items]
        reach = [a["reach_h"] for a in aps if a["reach_h"] is not None]
        over = [a["overshoot_c"] for a in aps]
        retires = Counter(r for a in aps for r in a["retires"])
        print(f"{variant:22} {label:9} {mean(w for _, w, _ in items):8.2f} {mean(x for *_, x in items):8.2f} "
              f"{(mean(reach) if reach else float('nan')):10.2f} {mean(over):8.3f} / {max(over):6.3f} "
              f"{mean(a['cap_min'] for a in aps):8.1f} {mean(a['cap_toggles'] for a in aps):9.1f} "
              f"{mean(a['water_limited_min'] for a in aps):13.1f} {sum(a['arms'] for a in aps):6d} "
              f"{sum(retires.values()):5d} {dict(retires) if retires else ''}"
              f"{'' if len(reach) == len(aps) else f'  (nie doszło: {len(aps) - len(reach)}/{len(aps)})'}")
