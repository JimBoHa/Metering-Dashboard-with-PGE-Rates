from __future__ import annotations
import asyncio
import contextlib
import csv
import io
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from .store import Store
from .models import Configuration, Reading, Expense, RateSelection
from .acquisition import Acquisition
from .billing import Accounting, local_midnight
from .rates import fetch_tariff, normalize_schedule
from .security import Security, COOKIE, password_hash, verify
from .sql_secrets import SqlSecrets
from .sql_trends import SqlTrends
from .sql_api import router as sql_router
from .monthly_api import router as monthly_router
from . import __version__

class ConfigUpdate(BaseModel):
    revision:str
    configuration:Configuration
class Discovery(BaseModel):
    source_id:str
    device:dict|None=None
    offset:int=Field(default=1,ge=1)
    limit:int=Field(default=40,ge=1,le=80)
class Login(BaseModel):password:str=Field(max_length=1000)
class PasswordChange(BaseModel):
    current_password:str
    new_password:str=Field(min_length=12,max_length=1000)


def create_app(directory=None,background=True,sql_connector=None,auth_required=None):
    directory=directory or os.environ.get("METERING_DATA_DIR",str(Path.home()/".local/share/metering-dashboard"))
    if auth_required is None:auth_required=os.environ.get("METERING_AUTH_DISABLED","").strip().lower() not in {"1","true","yes"}
    store=Store(directory);security=Security(store);collector=Acquisition(store)
    open_session={"csrf":secrets.token_urlsafe(24)}
    def active_session(request):return security.session(request.cookies.get(COOKIE)) if auth_required else open_session
    def session_info(session):return {"username":"admin","csrf":session["csrf"],"auth_required":auth_required}
    vault=SqlSecrets(store);sql=SqlTrends(store,vault,sql_connector)
    jobs={};job_tasks={}
    async def rates_loop():
        while True:
            cfg=store.config();sync=store.get("rate_sync",{})
            month=datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m")
            selection=next((r.selection for r in cfg.monthly_rates if r.month==month),cfg.rate)
            if selection.schedule and (time.time()-sync.get("last_attempt",0)>86400 or sync.get("selection")!=selection.model_dump()):
                store.set("rate_sync",{**sync,"last_attempt":time.time(),"selection":selection.model_dump()})
                try:await fetch_tariff(store,selection)
                except Exception as e:store.set("rate_sync",{**store.get("rate_sync",{}),"last_attempt":time.time(),"error":str(e)[:500]})
            await asyncio.sleep(60)
    @asynccontextmanager
    async def lifespan(app):
        tasks=[asyncio.create_task(collector.run()),asyncio.create_task(rates_loop()),asyncio.create_task(sql.loop())] if background else []
        yield
        tasks.extend(job_tasks.values())
        for task in tasks:task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):await task
        await sql.close()
        collector.close();store.close()
    app=FastAPI(title="Metering Dashboard",lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.store=store;app.state.collector=collector;app.state.sql=sql;app.state.auth_required=auth_required
    @app.exception_handler(RequestValidationError)
    async def validation_error(request,error):
        return JSONResponse({"detail":[{k:e[k] for k in ("loc","msg","type") if k in e} for e in error.errors()]},422)
    @app.middleware("http")
    async def guard(request,call_next):
        path=request.url.path
        headers={"X-Content-Type-Options":"nosniff","Referrer-Policy":"same-origin","Content-Security-Policy":"default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'","Cache-Control":"no-store"}
        if path.startswith("/api/") and path not in {"/api/login","/api/health"}:
            session=active_session(request)
            if not session:return JSONResponse({"detail":"Sign in to continue"},401,headers=headers)
            if request.method not in {"GET","HEAD"} and request.headers.get("x-csrf-token")!=session["csrf"]:return JSONResponse({"detail":"Request verification failed; reload the dashboard"},403,headers=headers)
        if request.method not in {"GET","HEAD","OPTIONS"}:
            origin=request.headers.get("origin")
            if origin and urlparse(origin).netloc!=request.headers.get("host"):return JSONResponse({"detail":"Cross-origin request rejected"},403,headers=headers)
            if int(request.headers.get("content-length","0"))>2_000_000:return JSONResponse({"detail":"Request too large"},413,headers=headers)
        response=await call_next(request)
        for k,v in headers.items():response.headers[k]=v
        return response
    @app.get("/api/health")
    def health():return {"service":"metering-dashboard","status":"running","version":__version__}
    @app.post("/api/login")
    def login(body:Login,request:Request,response:Response):
        if not auth_required:return session_info(open_session)
        try:result=security.login(body.password,request.client.host)
        except ValueError as e:raise HTTPException(429,str(e))
        if not result:raise HTTPException(401,"Incorrect password")
        token,csrf=result
        response.set_cookie(COOKIE,token,httponly=True,samesite="strict",secure=request.url.scheme=="https",max_age=8*3600)
        return session_info({"csrf":csrf})
    @app.get("/api/session")
    def session(request:Request):return session_info(active_session(request))
    @app.post("/api/logout")
    def logout(request:Request,response:Response):
        security.sessions.pop(request.cookies.get(COOKIE),None);response.delete_cookie(COOKIE);return {"ok":True}
    @app.post("/api/password")
    def change_password(body:PasswordChange):
        if not auth_required:raise HTTPException(409,"Password changes are unavailable while sign-in is disabled")
        if not verify(body.current_password,store.get("password")):raise HTTPException(400,"Current password is incorrect")
        store.set("password",password_hash(body.new_password));security.sessions.clear()
        (store.directory/"initial-access.txt").unlink(missing_ok=True)
        store.audit("password.change",{})
        return {"ok":True}
    @app.get("/api/config")
    def config():return {"configuration":vault.public_config(store.config()),"revision":store.revision()}
    def save_configuration(cfg,revision):
        with store.lock,store.db:
            if revision!=store.revision():raise HTTPException(409,"Configuration changed in another session; refresh before saving")
            meters={m.id:m for m in cfg.meters}
            def descendants(mid):return {t.meter_id for t in meters[mid].terms}|{x for t in meters[mid].terms for x in descendants(t.meter_id)}
            for m in cfg.meters:
                if m.billable and any(meters[x].billable for x in descendants(m.id)):raise HTTPException(400,"Select either the net meter or its inputs for electric billing to avoid counting the same energy twice")
            if cfg.rate.schedule:
                try:cfg.rate.schedule=normalize_schedule(cfg.rate.schedule)
                except ValueError as e:raise HTTPException(400,str(e))
            try:updates=vault.prepare(cfg)
            except ValueError as e:raise HTTPException(400,str(e))
            for meter in cfg.meters:
                if meter.source_id and next(s for s in cfg.sources if s.id==meter.source_id).kind=="sql_server":
                    if not all(k in meter.point for k in ("point_id","sql_profile","mapping_signature","expected_name","expected_units")):
                        raise HTTPException(400,"Choose SQL trend points through the connection's point tree")
            old=store.config()
            vault.write(updates,cfg)
            store.db.execute("INSERT INTO settings VALUES('configuration',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(cfg.model_dump_json(),))
            if old.rate!=cfg.rate:store.db.execute("INSERT INTO settings VALUES('rate_sync','{}') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        store.audit("configuration.save",{"meter_count":len(cfg.meters),"source_count":len(cfg.sources)})
        return config()
    @app.put("/api/config")
    def save_config(body:ConfigUpdate):return save_configuration(body.configuration,body.revision)
    app.include_router(sql_router(store,sql,save_configuration))
    app.include_router(monthly_router(store,job_tasks))
    @app.get("/api/status")
    def status():return {"collector":{**collector.status,**sql.status},"sql_jobs":sql.list_jobs(),"last_poll":collector.last_poll,"latest":store.latest(),"rate_sync":store.get("rate_sync",{}),"tariffs":store.tariffs()}
    @app.post("/api/discover")
    async def discover(body:Discovery):
        source=next((s for s in store.config().sources if s.id==body.source_id),None)
        if not source:raise HTTPException(404,"Unknown source")
        try:return await collector.discover(source,body.device,body.offset,body.limit)
        except Exception as e:raise HTTPException(400,str(e)[:500])
    @app.post("/api/discovery-jobs")
    async def start_discovery_job(body:Discovery):
        import secrets
        source=next((s for s in store.config().sources if s.id==body.source_id),None)
        if not source or source.kind!="bacnet" or not body.device:raise HTTPException(400,"Choose a BACnet device to inventory")
        if any(not job["done"] for job in jobs.values()):raise HTTPException(409,"An inventory scan is already running")
        for key in list(jobs)[:-5]:jobs.pop(key,None);job_tasks.pop(key,None)
        jid=secrets.token_hex(8)
        jobs[jid]={"id":jid,"done":False,"offset":1,"object_count":None,"points":[],"error":None}
        async def scan():
            try:
                offset=1
                while True:
                    result=await collector.discover(source,body.device,offset,80)
                    job=jobs[jid];job["points"].extend(result["points"]);job["object_count"]=result["object_count"];job["offset"]=min(offset+79,result["object_count"])
                    offset=result["next_offset"]
                    if not offset:break
                    if offset>10000:job["error"]="Stopped at 10000 objects; use paged discovery for the remainder";break
                    await asyncio.sleep(.1)
            except asyncio.CancelledError:jobs[jid]["error"]="Scan cancelled";raise
            except Exception as e:jobs[jid]["error"]=str(e)[:500]
            finally:jobs[jid]["done"]=True
        job_tasks[jid]=asyncio.create_task(scan())
        return {"id":jid}
    @app.get("/api/discovery-jobs/{job_id}")
    def discovery_job(job_id:str):
        if job_id not in jobs:raise HTTPException(404,"Inventory job not found")
        return jobs[job_id]
    @app.delete("/api/discovery-jobs/{job_id}")
    def stop_discovery_job(job_id:str):
        if job_id not in job_tasks:raise HTTPException(404,"Inventory job not found")
        job_tasks[job_id].cancel();return {"ok":True}
    @app.post("/api/verify-point")
    async def verify_point(body:dict):
        source=next((s for s in store.config().sources if s.id==body.get("source_id")),None)
        if not source or source.kind!="bacnet":raise HTTPException(400,"Choose a BACnet source")
        try:
            p=body["point"];obj=(p["object_type"],int(p["object_instance"]))
            async with collector.lock:
                app=await collector.bacnet(source)
                name=str(await collector.read_property(app,p["address"],obj,"object-name"))
                units=str(await collector.read_property(app,p["address"],obj,"units"))
                return {**p,"expected_name":name,"expected_units":units}
        except Exception as e:raise HTTPException(400,str(e)[:500])
    @app.post("/api/poll")
    async def poll():
        if collector.lock.locked():raise HTTPException(409,"A meter read or discovery is already running")
        await collector.poll();return status()
    @app.post("/api/readings")
    def readings(body:list[Reading]):
        if len(body)>10000:raise HTTPException(400,"Import at most 10000 readings per request")
        meters={m.id:m for m in store.config().meters}
        for r in body:
            if r.meter_id not in meters or meters[r.meter_id].mode=="virtual":raise HTTPException(400,"Readings require an existing physical meter")
            if r.timestamp>time.time()+60:raise HTTPException(400,"Reading timestamp is in the future")
        for r in body:store.add_reading(r)
        store.audit("readings.import",{"count":len(body)})
        return {"accepted":len(body),"note":"Duplicate meter/timestamp pairs are ignored"}
    @app.post("/api/rates/refresh")
    async def rate_refresh(body:RateSelection):
        try:
            result=await fetch_tariff(store,body)
            return result
        except Exception as e:
            store.set("rate_sync",{**store.get("rate_sync",{}),"last_attempt":time.time(),"error":str(e)[:500]})
            raise HTTPException(400,str(e)[:500])
    @app.get("/api/expenses")
    def expenses():return store.expenses()
    @app.put("/api/expenses/{expense_id}")
    def save_expense(expense_id:str,body:Expense):
        if expense_id!=body.id:raise HTTPException(400,"Expense ID mismatch")
        cfg=store.config();meters={m.id:m for m in cfg.meters}
        if any(a.meter_id not in meters for a in body.allocations):raise HTTPException(400,"Unknown allocation meter")
        if len({meters[a.meter_id].unit for a in body.allocations})>1:raise HTTPException(400,"Allocate a bill among meters with matching usage units")
        # Reject overlapping parent/child formulas in a single expense pool.
        def descendants(mid):return {t.meter_id for t in meters[mid].terms}|{x for t in meters[mid].terms for x in descendants(t.meter_id)}
        chosen={a.meter_id for a in body.allocations}
        if any(chosen & descendants(mid) for mid in chosen):raise HTTPException(400,"An allocation pool cannot include a net meter together with its own inputs")
        with store.lock,store.db:
            store.db.execute("INSERT INTO expenses VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",(body.id,body.model_dump_json()))
        store.audit("expense.save",{"id":body.id})
        return Accounting(store).allocate_expense(body)
    @app.delete("/api/expenses/{expense_id}")
    def delete_expense(expense_id:str):
        with store.lock,store.db:store.db.execute("DELETE FROM expenses WHERE id=?",(expense_id,))
        store.audit("expense.delete",{"id":expense_id});return {"ok":True}
    def period(start,end):
        try:
            s=datetime.strptime(start,"%Y-%m-%d").date();e=datetime.strptime(end,"%Y-%m-%d").date()
            a,b=local_midnight(s,store.config().timezone),local_midnight(e,store.config().timezone)
            if not 0<b-a<=367*86400:raise ValueError("Choose an increasing period of at most one year")
            return a,b
        except ValueError as e:raise HTTPException(400,str(e))
    @app.get("/api/report")
    def report(start:str,end:str):return Accounting(store).report(*period(start,end))
    @app.get("/api/report.csv")
    def report_csv(start:str,end:str):
        data=Accounting(store).report(*period(start,end));output=io.StringIO();writer=csv.writer(output)
        writer.writerow(["Meter","System","Quantity","Unit","Coverage","Base cost","Markup","Total cost","Issues"])
        def safe(value):
            s=str(value)
            return "'"+s if s.startswith(("=","+","-","@","\t","\r")) else s
        for r in data["meters"]:writer.writerow([safe(r["name"]),safe(r["category"]),r["quantity"],r["unit"],r["coverage"],r["base_cost"],r["markup"],r["total_cost"],safe("; ".join(r["issues"]))])
        return Response(output.getvalue(),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=billing-report.csv"})
    @app.get("/api/history/{meter_id}")
    def history(meter_id:str,start:str,end:str):
        if meter_id not in {m.id for m in store.config().meters}:raise HTTPException(404,"Unknown meter")
        a,b=period(start,end);rows=store.readings(meter_id,a,b)
        # Display a bounded series while all underlying readings remain in SQLite.
        stride=max(1,len(rows)//1500)
        return {"readings":rows[::stride],"total_samples":len(rows),"display_stride":stride}
    @app.get("/api/audit")
    def audit():
        with store.lock:return [dict(r) for r in store.db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 100")]
    static=Path(__file__).parent/"static"
    @app.get("/")
    def index():return FileResponse(static/"index.html")
    app.mount("/static",StaticFiles(directory=static),name="static")
    return app
