"""Authenticated SQL configuration, discovery and import endpoints."""
import time
from datetime import date
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from .models import Meter, Configuration
from .billing import local_midnight
from .sql_server import SqlTrendError

class ImportRequest(BaseModel):
    source_id:str
    meter_ids:list[str]|None=None
    start:date|None=None
    end:date|None=None
    @model_validator(mode='after')
    def dates(self):
        if (self.start is None)!=(self.end is None):raise ValueError('Supply both start and end dates')
        if self.start and self.end<=self.start:raise ValueError('End date is exclusive and must be after start')
        return self
class AddSqlMeters(ImportRequest):
    revision:str
    meters:list[Meter]=Field(min_length=1,max_length=50)

def router(store,trends,save_configuration):
    routes=APIRouter(prefix='/api/sql')
    def bounds(body):
        if not body.start:return None,None
        zone=store.config().timezone
        start=local_midnight(body.start,zone);end=min(time.time(),local_midnight(body.end,zone))
        if not 0<start<end or end-start>3661*86400:raise SqlTrendError('Choose a historical period of at most ten years')
        return start,end
    @routes.get('/jobs')
    def jobs():return trends.list_jobs()
    @routes.get('/jobs/{job_id}')
    def job(job_id:str):
        if job_id not in trends.jobs:raise HTTPException(404,'SQL job not found')
        return trends.public_job(trends.jobs[job_id])
    @routes.post('/jobs/{job_id}/cancel')
    def cancel(job_id:str):
        try:return trends.cancel(job_id)
        except SqlTrendError as e:raise HTTPException(400,str(e))
    @routes.post('/jobs/{job_id}/resume')
    async def resume(job_id:str):
        try:return trends.resume(job_id)
        except SqlTrendError as e:raise HTTPException(400,str(e))
    @routes.get('/{source_id}/inspect')
    async def inspect(source_id:str):
        try:return await trends.read(source_id,'inspect')
        except (SqlTrendError,ValueError) as e:raise HTTPException(400,str(e))
    @routes.get('/{source_id}/points')
    async def points(source_id:str,q:str='',offset:int=0,limit:int=500):
        if len(q)>200 or offset<0 or not 1<=limit<=1000:raise HTTPException(400,'Invalid point filter or page size')
        try:return await trends.read(source_id,'points',q,offset,limit)
        except (SqlTrendError,ValueError) as e:raise HTTPException(400,str(e))
    @routes.post('/imports')
    async def import_trends(body:ImportRequest):
        try:
            mids=body.meter_ids or [m.id for m in store.config().meters if m.source_id==body.source_id and m.enabled]
            return trends.start(body.source_id,mids,*bounds(body))
        except SqlTrendError as e:raise HTTPException(400,str(e))
    @routes.post('/meters')
    async def add_meters(body:AddSqlMeters):
        try:
            trends.source(body.source_id);span=bounds(body)
            if any(m.source_id!=body.source_id or m.mode=='virtual' for m in body.meters):raise SqlTrendError('Choose physical meters from the selected SQL source')
            cfg=store.config();existing={m.id for m in cfg.meters}
            if any(m.id in existing for m in body.meters):raise SqlTrendError('A meter with this ID already exists')
            cfg.meters.extend(body.meters);cfg=Configuration.model_validate(cfg.model_dump())
            result=save_configuration(cfg,body.revision)
            try:
                job=trends.start(body.source_id,[m.id for m in body.meters],*span)
                message='Meters created; SQL import started'
            except SqlTrendError:
                job=None;message='Meters created. Another import is active; use Import trends after it finishes.'
            return {**result,'job':job,'message':message}
        except SqlTrendError as e:raise HTTPException(400,str(e))
    return routes
