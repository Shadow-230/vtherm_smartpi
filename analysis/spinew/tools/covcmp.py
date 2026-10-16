"""Compare coverage: per file against the baseline, and the fork's own lines (diff from 6a131df)."""
import json, re, subprocess, sys
base_json, new_json, checkout = sys.argv[1:4]
B = json.load(open(base_json))["files"]; N = json.load(open(new_json))["files"]
def pct(f, k):
    s = f["summary"]
    if k == "l": return 100.0 * s["covered_lines"] / s["num_statements"] if s["num_statements"] else 100.0
    return 100.0 * s["covered_branches"] / s["num_branches"] if s["num_branches"] else 100.0
drops = []
for name, f in N.items():
    if name in B:
        for k in ("l", "b"):
            if pct(f, k) < pct(B[name], k) - 0.05:
                drops.append((name, k, round(pct(B[name], k), 1), round(pct(f, k), 1)))
print("drops:", drops or "none")
diff = subprocess.run(["git", "-C", checkout, "diff", "-U0", "6a131df", "HEAD", "--", "custom_components/vtherm_smartpi"],
                      capture_output=True, text=True).stdout
own = {}; cur = None
for line in diff.splitlines():
    if line.startswith("+++ b/"): cur = line[6:]; own.setdefault(cur, set())
    m = re.match(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", line)
    if m and cur:
        start, cnt = int(m.group(1)), int(m.group(2) or 1)
        own[cur].update(range(start, start + cnt))
lc = lt = bc = bt = 0; gaps = {}
for name, lines in own.items():
    f = N.get(name)
    if not f: continue
    ex = set(f["executed_lines"]); miss = set(f["missing_lines"])
    stm = (ex | miss) & lines
    lc += len(stm & ex); lt += len(stm)
    for a, b in f.get("executed_branches", []):
        if a in lines: bc += 1; bt += 1
    for a, b in f.get("missing_branches", []):
        if a in lines: bt += 1; gaps.setdefault(name, []).append(f"{a}->{b}")
    for l in sorted(stm & miss): gaps.setdefault(name, []).append(str(l))
print(f"fork lines {lc}/{lt} ({100*lc/lt:.1f} %), branches {bc}/{bt} ({100*bc/bt:.1f} %)")
for k, v in gaps.items(): print("  gap", k, ", ".join(v[:20]))
t = json.load(open(new_json))["totals"]; tb = json.load(open(base_json))["totals"]
print(f"total {tb['percent_covered']:.2f} -> {t['percent_covered']:.2f}")
