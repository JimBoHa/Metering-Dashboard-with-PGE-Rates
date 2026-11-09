"""Time-aware quantity accounting and cent-balanced shared bill allocation.

Counter deltas assume uniform consumption between adjacent verified samples. Rate
samples use trapezoidal integration. Missing spans and resets are never bridged.
"""
from datetime import datetime, time as daytime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from .rates import period_at


def local_midnight(day, zone):
    return datetime.combine(day, daytime(), ZoneInfo(zone)).timestamp()


def money(n):
    return float(Decimal(str(n)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class Accounting:
    def __init__(self, store, config=None):
        self.store = store
        self.config = config or store.config()
        self.meters = {m.id:m for m in self.config.meters}
        self.cache = {}

    def segments(self, mid, start, end):
        key = (mid,start,end)
        if key in self.cache: return self.cache[key]
        m = self.meters[mid]
        result, issues = [], set()
        if not m.enabled:
            return [], {"Meter disabled"}
        if m.mode == "virtual":
            inputs = [(t,self.segments(t.meter_id,start,end)) for t in m.terms if t.factor]
            for _,(_,notes) in inputs: issues.update(notes)
            edges = sorted({start,end} | {x for _,(segs,_) in inputs for s in segs for x in s[:2]})
            cursors = [0] * len(inputs)
            for a,b in zip(edges,edges[1:]):
                quantity = 0
                for i,(term,(segs,_)) in enumerate(inputs):
                    while cursors[i]<len(segs) and segs[cursors[i]][1]<=a: cursors[i]+=1
                    if cursors[i]>=len(segs): break
                    s=segs[cursors[i]]
                    if s[0]>a or s[1]<b: break
                    quantity += term.factor*s[2]*(b-a)/(s[1]-s[0])
                else:
                    if inputs: result.append((a,b,quantity))
            if not inputs: issues.add("No formula inputs")
        else:
            rows = self.store.readings(mid,start,end)
            if m.mode == "interval":
                for r in rows:
                    if r["quality"] != "good":
                        issues.add("Unverified or failed readings excluded"); continue
                    a,b=r["timestamp"]-m.interval_seconds,r["timestamp"]
                    lo,hi=max(a,start),min(b,end)
                    if hi>lo:
                        if result and lo<result[-1][1]:
                            issues.add("Overlapping interval readings excluded"); continue
                        q=r["value"]*m.scale+m.offset
                        result.append((lo,hi,q*(hi-lo)/(b-a)))
            else:
                for r1,r2 in zip(rows,rows[1:]):
                    a,b = r1["timestamp"],r2["timestamp"]
                    lo,hi = max(a,start),min(b,end)
                    if hi<=lo: continue
                    if any(r["quality"]!="good" for r in (r1,r2)):
                        issues.add("Unverified or failed readings excluded"); continue
                    if r1.get("stream_id")!=r2.get("stream_id"):
                        issues.add("Source configuration changed; transition excluded");continue
                    if b-a>m.max_gap_seconds:
                        issues.add("Collection gaps excluded"); continue
                    v1,v2=(r1["value"]*m.scale+m.offset),(r2["value"]*m.scale+m.offset)
                    if m.mode == "counter":
                        delta=v2-v1
                        if delta<0:
                            if m.rollover and 0<=v1<=m.rollover and 0<=v2<=m.rollover:
                                delta=m.rollover-v1+v2; issues.add("Configured counter rollover applied")
                            else:
                                issues.add("Counter reset excluded"); continue
                        result.append((lo,hi,delta*(hi-lo)/(b-a)))
                    else:
                        left=v1+(v2-v1)*(lo-a)/(b-a)
                        right=v1+(v2-v1)*(hi-a)/(b-a)
                        # Subdivide before TOU pricing; preserve a linear rate in every bin.
                        cursor=lo
                        while cursor<hi:
                            stop=min(hi,(int(cursor//900)+1)*900)
                            lv=left+(right-left)*(cursor-lo)/(hi-lo)
                            rv=left+(right-left)*(stop-lo)/(hi-lo)
                            result.append((cursor,stop,(lv+rv)/2*(stop-cursor)/m.rate_period_seconds))
                            cursor=stop
        self.cache[key] = result,issues
        return result,issues

    def usage(self, mid, start, end):
        segs,issues=self.segments(mid,start,end)
        q=sum(s[2] for s in segs)
        covered=sum(s[1]-s[0] for s in segs)
        if covered<end-start-0.1: issues.add("Incomplete interval coverage")
        if q<0: issues.add("Negative net usage; check supply/return mapping")
        return {"quantity":q,"coverage":min(1,covered/(end-start)),"issues":sorted(issues)}

    def tariff_for(self, timestamp, tariffs):
        d=datetime.fromtimestamp(timestamp,ZoneInfo("America/Los_Angeles")).date().isoformat()
        selection=self.config.rate.model_dump()
        candidates=[t for t in tariffs if t["selection"]==selection and t["effective_from"]<=d and (not t.get("effective_to") or d<t["effective_to"])]
        return max(candidates,key=lambda t:(t["effective_from"],t["fetched_at"])) if candidates else None

    def electricity(self, mid,start,end,tariffs):
        m=self.meters[mid]
        segs,issues=self.segments(mid,start,end)
        total=0.; breakdown={}; unpriced=0.; unpriced_seconds=0.; demand_bins={}; used={}
        for a,b,q in segs:
            cursor=a
            while cursor<b:
                stop=min(b,(int(cursor//900)+1)*900)
                qty=q*(stop-cursor)/(b-a)
                tariff=self.tariff_for(cursor,tariffs)
                if tariff:
                    season,period=period_at(cursor,tariff["selection"]["schedule"],"America/Los_Angeles")
                    rate=tariff["energy"].get(season,{}).get(period)
                else: rate=None
                if rate is None:
                    unpriced+=abs(qty);unpriced_seconds+=stop-cursor
                else:
                    cost=qty*rate;total+=cost
                    label=f"{season} / {period}"
                    row=breakdown.setdefault(label,{"quantity":0.,"cost":0.})
                    row["quantity"]+=qty;row["cost"]+=cost
                    used[tariff["id"]]=tariff
                    k=int(cursor//900)
                    item=demand_bins.setdefault(k,{"qty":0.,"seconds":0.,"tariff":tariff,"season":season,"period":period})
                    item["qty"]+=qty;item["seconds"]+=stop-cursor
                cursor=stop
        # Demand and daily fixed charges require whole local calendar-month coverage.
        local_start=datetime.fromtimestamp(start,ZoneInfo("America/Los_Angeles"))
        local_end=datetime.fromtimestamp(end,ZoneInfo("America/Los_Angeles"))
        whole_month=(local_start.day==1 and local_start.time()==daytime() and local_end.day==1 and local_end.time()==daytime() and (local_end.year-local_start.year)*12+local_end.month-local_start.month==1)
        demand_cost,fixed_cost=0.,0.
        demand_lines=[]
        full=self.usage(mid,start,end)["coverage"]>=0.999999
        if m.electricity_charges=="account" and whole_month and full and len(used)==1 and not unpriced_seconds:
            tariff=next(iter(used.values()))
            for charge in tariff.get("demand",[]):
                eligible=[]
                for k,bin in demand_bins.items():
                    if bin["seconds"]<899.99 or bin["season"]!=charge["season"]: continue
                    if charge["period"] not in ("maximum","maximum_excluding_solar",bin["period"]): continue
                    dt=datetime.fromtimestamp(k*900,ZoneInfo("America/Los_Angeles"))
                    if charge["period"]=="maximum_excluding_solar" and 9<=dt.hour<14: continue
                    eligible.append((dt.date(),bin["qty"]*4))
                if not eligible: continue
                if charge["basis"]=="daily":
                    peaks={}
                    for day,kw in eligible: peaks[day]=max(peaks.get(day,0),kw)
                    basis=sum(peaks.values())
                else: basis=max(kw for _,kw in eligible)
                cost=basis*charge["price"];demand_cost+=cost
                demand_lines.append({**charge,"billed_kw":basis,"cost":cost})
            fixed_cost=tariff.get("customer_daily",0)*(local_end.date()-local_start.date()).days
        notes=[]
        if m.electricity_charges=="energy_only": notes.append("Energy-only pricing; allocate shared account charges through Bills & allocation")
        if m.electricity_charges=="account" and not whole_month: notes.append("Demand and fixed charges require one complete calendar month")
        if m.electricity_charges=="account" and len(used)>1: notes.append("Tariff changed during period; demand and fixed charges require invoice reconciliation")
        if not full: notes.append("Incomplete readings: charges are estimates; demand and fixed charges excluded")
        if unpriced_seconds: notes.append("Some usage has no applicable imported tariff")
        return {"energy_cost":total,"demand_cost":demand_cost,"fixed_cost":fixed_cost,"base_cost":total+demand_cost+fixed_cost,"unpriced_quantity":unpriced,"unpriced_seconds":unpriced_seconds,"periods":breakdown,"demand":demand_lines,"notes":notes,"tariff_ids":list(used)}

    def allocate_expense(self,e):
        start=local_midnight(e.period_start,self.config.timezone)
        end=local_midnight(e.period_end,self.config.timezone)
        rows=[]; problems=[];units=set()
        for a in e.allocations:
            if a.meter_id not in self.meters:
                problems.append("Allocation references a removed meter"); continue
            m=self.meters[a.meter_id];units.add(m.unit)
            u=self.usage(m.id,start,end)
            if u["coverage"]<0.999999 or u["quantity"]<0: problems.append(f"{m.name}: complete nonnegative usage is required")
            rows.append({"meter_id":m.id,"quantity":u["quantity"],"weight":a.weight,"basis":u["quantity"]*a.weight})
            self.cache.clear()
        denominator=sum(r["basis"] for r in rows)
        if len(units)>1: problems.append("Allocation meters must use the same units")
        if denominator<=0: problems.append("No positive usage to allocate")
        if problems: return {"expense_id":e.id,"amount":e.amount,"allocated":False,"issues":problems,"rows":rows}
        cents=int(Decimal(str(e.amount)).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)*100)
        amounts=[Decimal(str(r["basis"]))/Decimal(str(denominator))*cents for r in rows]
        shares=[int(a) for a in amounts]
        for i in sorted(range(len(rows)),key=lambda i:amounts[i]-shares[i],reverse=True)[:cents-sum(shares)]: shares[i]+=1
        for r,share in zip(rows,shares):r["share"]=r["basis"]/denominator;r["base_cost"]=share/100
        return {"expense_id":e.id,"amount":e.amount,"allocated":True,"unit_cost":e.amount/denominator,"unit":next(iter(units)),"rows":rows,"issues":[]}

    def report(self,start,end):
        from .models import Expense
        tariffs=self.store.tariffs();latest=self.store.latest();rows=[]
        for m in self.config.meters:
            u=self.usage(m.id,start,end)
            electrical=self.electricity(m.id,start,end,tariffs) if m.unit=="kWh" and m.billable else None
            rows.append({"meter_id":m.id,"name":m.name,"category":m.category,"unit":m.unit,"billable":m.billable,**u,"latest":latest.get(m.id),"electricity":electrical,"expense_cost":0.})
            self.cache.clear()
        allocations=[];byid={r["meter_id"]:r for r in rows};notes=[]
        for data in self.store.expenses():
            e=Expense.model_validate(data)
            es,ee=local_midnight(e.period_start,self.config.timezone),local_midnight(e.period_end,self.config.timezone)
            if es>=start and ee<=end:
                a=self.allocate_expense(e);allocations.append({**a,"description":e.description})
                if a["allocated"]:
                    for share in a["rows"]:
                        byid[share["meter_id"]]["expense_cost"]+=share["base_cost"]
            elif es<end and ee>start: notes.append(f"{e.description}: bill period extends beyond report; choose its full period to allocate")
        for r in rows:
            m=self.meters[r["meter_id"]]
            r["base_cost"]=(r["electricity"]["base_cost"] if r["electricity"] else 0)+r["expense_cost"]
            pct=m.markup_percent if m.markup_percent is not None else self.config.markup_percent
            r["markup_percent"]=pct;r["markup"]=money(r["base_cost"]*pct/100)
            r["total_cost"]=money(r["base_cost"])+r["markup"]
        # Only explicitly selected billing meters contribute energy charges. Expense rows
        # contribute their allocated share independently of the energy-billing flag.
        return {"start":start,"end":end,"timezone":self.config.timezone,"meters":rows,"allocations":allocations,"notes":notes,"total_cost":money(sum(r["total_cost"] for r in rows)),"base_cost":money(sum(r["base_cost"] for r in rows)),"markup":money(sum(r["markup"] for r in rows)),"complete":all(r["coverage"]>=0.999999 and r["quantity"]>=0 for r in rows if r["billable"]) and all(a["allocated"] for a in allocations) and all(not r["electricity"] or not r["electricity"]["unpriced_seconds"] for r in rows)}
