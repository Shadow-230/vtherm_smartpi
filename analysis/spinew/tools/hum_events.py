"""Humidity events in raw samples: rise >= RISE %RH within WIN min; end when back within BACK %RH
of the pre-event level (or MAXH h). Prints per sensor: events/day, rise, peak, duration."""
import csv,sys,datetime as dt,collections,statistics as st
F=sys.argv[1]; RISE=float(sys.argv[2]); WIN=float(sys.argv[3]); BACK=float(sys.argv[4]); MAXH=float(sys.argv[5])
START=dt.datetime(2026,8,21,6,tzinfo=dt.timezone.utc)
rows=collections.defaultdict(list)
for r in csv.DictReader(open(F)):
    try: v=float(r["state"])
    except: continue
    t=dt.datetime.fromisoformat(r["last_changed"].replace("Z","+00:00"))
    if t>=START: rows[r["entity_id"]].append((t.timestamp(),v))
for e,v in sorted(rows.items()):
    v.sort(); events=[]; i=0; n=len(v)
    while i<n:
        t0,h0=v[i]
        # baseline = min over previous 15 min (held value)
        j=i+1; ev=None
        while j<n and v[j][0]-t0<=WIN*60:
            if v[j][1]-h0>=RISE: ev=j; break
            j+=1
        if ev is None: i+=1; continue
        base=min(x[1] for x in v[max(0,i-5):i+1])
        peak=h0; k=ev; tpeak=v[ev][0]
        while k<n and v[k][0]-t0<=MAXH*3600:
            if v[k][1]>peak: peak,tpeak=v[k][1],v[k][0]
            if v[k][1]<=base+BACK and v[k][0]>tpeak: break
            k+=1
        end=v[min(k,n-1)][0]
        events.append((t0,base,peak,(v[ev][0]-t0)/60,(end-t0)/60, k>=n or v[min(k,n-1)][0]-t0>MAXH*3600))
        while i<n and v[i][0]<end: i+=1
    days=(v[-1][0]-v[0][0])/86400
    print(f"{e}: {len(events)} events in {days:.0f} d ({len(events)/days:.2f}/d)")
    if events:
        rises=[p-b for _,b,p,_,_,_ in events]; durs=[d for *_,d,_ in events]; capped=sum(1 for *_,c in events if c)
        print(f"   rise to peak median {st.median(rises):.1f} (min {min(rises):.1f}, max {max(rises):.1f}) %RH; trigger after {st.median([x[3] for x in events]):.1f} min; duration median {st.median(durs):.0f} min (max {max(durs):.0f}); capped at {MAXH} h: {capped}")
        hours=collections.Counter(dt.datetime.fromtimestamp(t,dt.timezone.utc).astimezone().hour for t,*_ in events)
        print("   hours:", sorted(hours.items()))
