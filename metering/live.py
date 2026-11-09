"""Operational readings and observed month-to-date totals, separate from billing."""
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from .billing import Accounting, local_midnight
from .acquisition import stream_fingerprint


def live_units(meter):
    if meter.mode!="rate":return meter.unit,1.
    if meter.unit=="kWh":return "kW",3600/meter.rate_period_seconds
    if meter.unit=="kWh-thermal":return "kW-thermal",3600/meter.rate_period_seconds
    if meter.unit in {"gal","L","m3"}:return meter.unit+"/min",60/meter.rate_period_seconds
    return meter.unit+"/h",3600/meter.rate_period_seconds


class LiveView:
    def __init__(self,store,config=None,status=None,now=None):
        self.store=store;self.config=config or store.config();self.now=time.time() if now is None else now
        self.meters={m.id:m for m in self.config.meters};self.sources={s.id:s for s in self.config.sources}
        self.latest=store.latest();self.status=status or {};self.cache={}

    def reading(self,mid):
        if mid in self.cache:return self.cache[mid]
        meter=self.meters[mid]
        unit,conversion=live_units(meter)
        result={"meter_id":mid,"value":None,"partial_value":None,"unit":unit,"quantity_unit":meter.unit,"timestamp":None,"quality":"missing","issues":[],"kind":meter.mode,"available_inputs":0,"required_inputs":0}
        if not meter.enabled:
            result.update(quality="disabled",issues=["Meter disabled"])
        elif meter.mode=="virtual":
            terms=[t for t in meter.terms if t.factor]
            inputs=[(term,self.reading(term.meter_id)) for term in terms]
            dimensions={(r["unit"],r["kind"]) for _,r in inputs}
            available=[(term,r) for term,r in inputs if r["value"] is not None]
            result.update(required_inputs=len(inputs),available_inputs=len(available))
            if len(dimensions)!=1:
                result.update(quality="incompatible",issues=["Live formulas need matching measurement types and live units; period usage is calculated separately"])
            else:
                unit,kind=next(iter(dimensions));result.update(unit=unit,kind=kind)
                if available:result["partial_value"]=sum(term.factor*r["value"] for term,r in available)
                result["issues"]=sorted({issue for _,r in inputs for issue in r["issues"]})
                if len(available)!=len(inputs):
                    result["issues"].append("A formula input is missing, stale or unavailable; the complete live total is not shown")
                elif inputs:
                    result.update(value=result["partial_value"],timestamp=min(r["timestamp"] for _,r in inputs),quality="good" if all(r["quality"]=="good" for _,r in inputs) else "unverified")
        else:
            row=self.latest.get(mid);source=self.sources.get(meter.source_id)
            if source and not source.enabled:result.update(quality="disabled",issues=["Source disabled"])
            elif row:
                result.update(timestamp=row["timestamp"],quality=row["quality"])
                if row.get("error"):result["issues"].append(row["error"])
                if source and source.kind!="sql_server" and row.get("stream_id")!=stream_fingerprint(meter,source):
                    result.update(quality="missing");result["issues"].append("Source mapping changed; waiting for a reading from the current configuration")
                elif self.status.get(mid,{}).get("quality") in {"offline","fault"}:
                    result.update(quality=self.status[mid]["quality"])
                    result["issues"].append(self.status[mid].get("error") or "Source unavailable")
                elif self.now-row["timestamp"]>meter.max_gap_seconds:
                    result.update(quality="stale");result["issues"].append("Latest sample is older than the meter's maximum gap")
                if result["quality"] in {"good","unverified"}:
                    result["value"]=(row["value"]*meter.scale+meter.offset)*conversion
        self.cache[mid]=result
        return result

    def summary(self):
        readings={mid:self.reading(mid) for mid in self.meters}
        local=datetime.fromtimestamp(self.now,ZoneInfo(self.config.timezone))
        start=local_midnight(local.date().replace(day=1),self.config.timezone)
        wanted=set()
        def include(mid):
            if mid in wanted:return
            wanted.add(mid)
            for term in self.meters[mid].terms:
                if term.factor:include(term.meter_id)
        for m in self.meters.values():
            if m.display_on_dashboard!="none":include(m.id)
        verified=Accounting(self.store,self.config);observed=Accounting(self.store,self.config,include_unverified=True)
        totals={}
        if self.now>start:
            for mid in wanted:
                good=verified.usage(mid,start,self.now);preview=observed.usage(mid,start,self.now)
                segments,_=observed.segments(mid,start,self.now)
                totals[mid]={**preview,"quantity":preview["quantity"] if preview["coverage"]>0 else None,"verified_quantity":good["quantity"] if good["coverage"]>0 else None,"verified_coverage":good["coverage"],"unit":self.meters[mid].unit,"first_interval":segments[0][0] if segments else None,"last_interval":segments[-1][1] if segments else None,"provisional":preview["coverage"]<.999999 or any("Unverified" in note for note in preview["issues"])}
        return {"as_of":self.now,"month_start":start,"timezone":self.config.timezone,"readings":readings,"month_to_date":totals}
