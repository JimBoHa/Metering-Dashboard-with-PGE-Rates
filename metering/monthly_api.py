"""Monthly billing reports and restart-aware historical tariff downloads."""
import asyncio
import csv
import io
import time
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from .billing import Accounting, money, rate_coverage
from .periods import months_between
from .rates import fetch_tariff_history


class HistoryImport(BaseModel):
    start_month:str
    end_month:str
    revision:str


def csv_safe(value):
    text=str(value if value is not None else "")
    return "'"+text if text.startswith(("=","+","-","@","\t","\r")) else text


def monthly_csv(report,detail="summary"):
    output=io.StringIO();writer=csv.writer(output)
    common=["Month","Period start","Period end (exclusive)","Billing timezone","Schedule","Option","Voltage","Service","Customer class","Phase"]
    def prefix(m):
        s=m["selection"] or {}
        return [m["month"],m["period_start"],m["period_end"],m["timezone"],s.get("schedule"),s.get("option"),s.get("voltage"),s.get("service"),s.get("customer_class"),s.get("phase")]
    if detail=="meters":
        writer.writerow(common+["Meter","System","Quantity","Unit","Coverage","Energy","Demand","Fixed","Allocated bills","Base cost","Markup","Total","Status","Issues","Tariff versions"])
        for month in report["months"]:
            for r in month["meters"]:
                e=r["electricity"] or {};notes=month["notes"]+r["issues"]+e.get("notes",[])
                status=("Complete" if month["complete"] else "Provisional") if e or r["expense_cost"] else "Not billed"
                writer.writerow([csv_safe(v) for v in prefix(month)+[r["name"],r["category"],r["quantity"],r["unit"],r["coverage"],money(e.get("energy_cost",0)),money(e.get("demand_cost",0)),money(e.get("fixed_cost",0)),r["expense_cost"],money(r["base_cost"]),r["markup"],r["total_cost"],status,"; ".join(notes),"; ".join(e.get("tariff_ids",[]))]])
    else:
        writer.writerow(common+["Billed electricity kWh","Energy","Demand","Fixed","Allocated bills","Base cost","Markup","Total","Status","Unpriced kWh","Notes","Tariff versions"])
        for m in report["months"]:
            notes=m["notes"]+[r["name"]+": "+note for r in m["meters"] if r["electricity"] or r["expense_cost"] for note in r["issues"]+(r["electricity"] or {}).get("notes",[])]
            values=prefix(m)+[m["electricity_kwh"],m["energy_cost"],m["demand_cost"],m["fixed_cost"],m["expense_cost"],m["base_cost"],m["markup"],m["total_cost"],"Complete" if m["complete"] else "Provisional",m["unpriced_quantity"],"; ".join(notes),"; ".join(m["rate_coverage"]["tariff_ids"])]
            writer.writerow([csv_safe(v) for v in values])
    return output.getvalue()


def router(store,job_tasks):
    routes=APIRouter();key="historical_rate_import"
    previous=store.get(key)
    if previous and previous.get("status")=="running":
        store.set(key,{**previous,"status":"interrupted","active":"","error":"Service restarted; fetch historical rates again to continue with retained versions"})
    def checked_range(start,end):
        try:return months_between(start,end)
        except ValueError as e:raise HTTPException(400,str(e))
    @routes.get("/api/monthly-report")
    def report(start_month:str,end_month:str):
        checked_range(start_month,end_month)
        with store.lock:revision=store.revision();cfg=store.config()
        return {**Accounting(store,cfg).monthly_report(start_month,end_month),"configuration_revision":revision}
    @routes.get("/api/rates/monthly-coverage")
    def monthly_coverage(start_month:str,end_month:str):
        periods=checked_range(start_month,end_month)
        with store.lock:cfg=store.config();tariffs=store.tariffs();revision=store.revision()
        assignments={r.month:r.selection.model_dump() for r in cfg.monthly_rates}
        return {"revision":revision,"months":[{"month":a.strftime("%Y-%m"),"selection":assignments.get(a.strftime("%Y-%m")),**rate_coverage(assignments.get(a.strftime("%Y-%m")),a,b,tariffs)} for a,b in periods]}
    @routes.get("/api/monthly-report.csv")
    def report_csv(start_month:str,end_month:str,detail:str="summary"):
        checked_range(start_month,end_month)
        if detail not in {"summary","meters"}:raise HTTPException(400,"Choose summary or meters CSV detail")
        data=Accounting(store).monthly_report(start_month,end_month)
        return Response(monthly_csv(data,detail),media_type="text/csv",headers={"Content-Disposition":f'attachment; filename="monthly-billing-{detail}.csv"'})
    @routes.get("/api/rates/history")
    def history_status():return store.get(key)
    @routes.post("/api/rates/history")
    async def import_history(body:HistoryImport):
        checked_range(body.start_month,body.end_month)
        with store.lock:
            if body.revision!=store.revision():raise HTTPException(409,"Configuration changed; save the monthly schedules again before importing")
            config=store.config()
        previous=store.get(key,{})
        if previous.get("status")=="running":raise HTTPException(409,"A historical rate import is already running")
        if not any(body.start_month<=r.month<body.end_month for r in config.monthly_rates):raise HTTPException(400,"Save a rate schedule for at least one requested month")
        job={"status":"running","start_month":body.start_month,"end_month":body.end_month,"started_at":time.time(),"processed":0,"total":0,"imported":0,"errors":[],"active":"Reading PG&E's historical rate index","revision":body.revision}
        store.set(key,job)
        def progress(update):
            job.update(update);store.set(key,job)
        async def run():
            try:
                result=await fetch_tariff_history(store,config,body.start_month,body.end_month,progress)
                job.update(status="completed" if result["complete"] else "partial",result=result)
            except asyncio.CancelledError:
                job.update(status="interrupted",error="Import interrupted; fetch again to continue with retained versions");raise
            except Exception as e:job.update(status="failed",error=str(e)[:500])
            finally:
                job.update(active="",finished_at=time.time());store.set(key,job)
        job_tasks["rate-history"]=asyncio.create_task(run())
        store.audit("tariff.history.start",{"start_month":body.start_month,"end_month":body.end_month})
        return job
    return routes
