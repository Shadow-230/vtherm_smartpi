"""Replay a Home Assistant humidity history (CSV) through SmartPI's HumidityEvent, one
calculation a minute; per sensor: events a day, hours gated a day, gated spans."""
import csv,sys,datetime as dt,collections,statistics as st
from custom_components.vtherm_smartpi.smartpi.humidity_event import HumidityEvent
START=dt.datetime(2026,8,21,6,tzinfo=dt.timezone.utc)
rows=collections.defaultdict(list)
for r in csv.DictReader(open(sys.argv[1])):
    try: v=float(r["state"])
    except: continue
    t=dt.datetime.fromisoformat(r["last_changed"].replace("Z","+00:00"))
    if t>=START: rows[r["entity_id"]].append((t.timestamp(),v))
for e,v in sorted(rows.items()):
    v.sort(); det=HumidityEvent(); t=v[0][0]; i=0; active=0; spans=[]; start=None; cur=None
    while t<=v[-1][0]:
        while i<len(v) and v[i][0]<=t: cur=v[i][1]; i+=1
        a=det.update(t,cur)
        if a and start is None: start=t
        if not a and start is not None: spans.append((t-start)/60); start=None
        active+=a; t+=60
    days=(v[-1][0]-v[0][0])/86400
    print(f"{e}: {det.count} events ({det.count/days:.2f}/d); gated {active/60/days:.1f} h/d; gated span median {st.median(spans):.0f} min, max {max(spans):.0f} min")
