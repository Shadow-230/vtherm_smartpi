import re, sys
rows = {}
for line in open(sys.argv[1]):
    m = re.match(r"SET (.+?) (split|indep): .*?steady=([\d.]+).*?over=(\S+) reach=(\S+)", line)
    if not m: continue
    name, kind, steady, over, reach = m.groups()
    ov = [float(x) for x in over.split("/")]
    rc = [None if x == "-" else float(x) for x in reach.split("/")]
    rows.setdefault(name, {})[kind] = (float(steady), max(ov), rc)
print(f"{'case':28} {'reach split':>11} {'reach ind':>9} {'Δ h':>6} {'over split':>10} {'over ind':>8} {'steady s/i':>12} ok")
for name, d in rows.items():
    if "split" not in d or "indep" not in d: continue
    (ss, so, sr), (is_, io, ir) = d["split"], d["indep"]
    avg = lambda r: sum(x for x in r if x is not None) / max(1, len([x for x in r if x is not None]))
    sa, ia = avg(sr), avg(ir)
    ok = (None not in sr) and sa <= ia and so < io and ss <= is_ + 0.0
    print(f"{name:28} {sa:11.2f} {ia:9.2f} {sa-ia:+6.2f} {so:+10.2f} {io:+8.2f} {ss:6.3f}/{is_:.3f} {'OK' if ok else '--'}")
