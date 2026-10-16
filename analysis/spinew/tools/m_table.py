import json, sys
base = json.load(open(sys.argv[1]))
vars_ = {p.split("m_")[-1].split(".json")[0]: json.load(open(p)) for p in sys.argv[2:]}
avg = lambda r: sum(x for x in r if x is not None)/max(1,len([x for x in r if x is not None]))
print(f"{'case':24} {'ind reach/over/night/heat':>28} | " + " | ".join(f"{k:>30}" for k in vars_))
for key, r in base.items():
    i = r["indep"]
    line = f"{key:24} {avg(i['reach']):5.2f} {max(i['over']):+.2f} {i['nmean']:5.2f} {i['heat_per_day']:5.1f} |"
    for k, v in vars_.items():
        s = (v.get(key) or {}).get("split") or (r["split"] if k == "off" else None)
        if not s: line += " " * 31 + "|"; continue
        ok = None not in s["reach"] and avg(s["reach"]) <= avg(i["reach"]) and max(s["over"]) < max(i["over"]) and s["steady"] <= i["steady"]
        line += f" {avg(s['reach']):5.2f} {max(s['over']):+.2f} {s['nmean']:5.2f} {s['heat_per_day']:5.1f} {s['steady']:.3f} {'OK' if ok else '--'} |"
    print(line)
