"""Durable, paged SQL imports and incremental sync, isolated from BACnet polling."""
from __future__ import annotations
import asyncio
import copy
import hashlib
import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from .models import Reading
from .sql_models import SqlSettings
from .sql_server import SqlReader, SqlTrendError


def sql_stream_id(meter,source):
    # Password rotation, TLS settings and sync scheduling do not change measurement identity.
    keys=('host','port','database','profile','mapping','timezone')
    document={'source_kind':'sql_server','source':{k:source.settings.get(k) for k in keys},'point':{k:v for k,v in meter.point.items() if k!='commissioned'},
              'mode':meter.mode,'unit':meter.unit,'scale':meter.scale,'offset':meter.offset,'rate_period_seconds':meter.rate_period_seconds,'interval_seconds':meter.interval_seconds}
    return hashlib.sha256(json.dumps(document,sort_keys=True).encode()).hexdigest()

class SqlTrends:
    def __init__(self,store,vault,connector=None):
        self.store=store;self.vault=vault;self.connector=connector;self.status={};self.tasks={};self.stopping=False
        self.executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='sql-trends')
        with store.lock,store.db:
            store.db.executescript('''CREATE TABLE IF NOT EXISTS sql_jobs(id TEXT PRIMARY KEY,updated REAL NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sql_watermarks(meter_id TEXT NOT NULL,stream_id TEXT NOT NULL,timestamp REAL NOT NULL,PRIMARY KEY(meter_id,stream_id));''')
            self.jobs={r['id']:json.loads(r['data']) for r in store.db.execute('SELECT * FROM sql_jobs ORDER BY updated DESC LIMIT 20')}
            for job in self.jobs.values():
                if job['status'] in ('queued','running'):
                    job['status']='interrupted';job['error']='Service restarted. Resume to continue from the saved page.';self.save_job(job)
    def source(self,source_id):
        source=next((s for s in self.store.config().sources if s.id==source_id),None)
        if not source or source.kind!='sql_server':raise SqlTrendError('Choose a SQL Server trend source')
        if not source.enabled:raise SqlTrendError('Enable the SQL source before connecting')
        return source
    def reader(self,source_id):
        source=self.source(source_id)
        return SqlReader(source.settings,self.vault.password(source_id),self.store.directory,self.connector)
    async def read(self,source_id,method,*args):
        def call():return getattr(self.reader(source_id),method)(*args)
        return await asyncio.get_running_loop().run_in_executor(self.executor,call)
    def save_job(self,job):
        job['updated']=time.time()
        with self.store.lock,self.store.db:
            self.store.db.execute('INSERT INTO sql_jobs VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,data=excluded.data',(job['id'],job['updated'],json.dumps(job,allow_nan=False)))
    def public_job(self,job):
        return {k:copy.deepcopy(v) for k,v in job.items() if k not in ('signatures',)}
    def list_jobs(self):return [self.public_job(j) for j in sorted(list(self.jobs.values()),key=lambda j:j['updated'],reverse=True)[:20]]
    def selected(self,source_id,meter_ids):
        source=self.source(source_id);meters={m.id:m for m in self.store.config().meters}
        if not meter_ids or len(set(meter_ids))!=len(meter_ids) or len(meter_ids)>500:raise SqlTrendError('Choose distinct SQL meters to import')
        if any(mid not in meters or meters[mid].source_id!=source_id or not meters[mid].enabled or meters[mid].mode=='virtual' for mid in meter_ids):raise SqlTrendError('Every selected meter must be an enabled physical input from this SQL source')
        return source,[meters[mid] for mid in meter_ids]
    def start(self,source_id,meter_ids,start=None,end=None,automatic=False):
        source,meters=self.selected(source_id,meter_ids);now=time.time();end=min(now,end or now)
        if any(j['source_id']==source_id and j['status'] in ('queued','running') for j in self.jobs.values()):raise SqlTrendError('This SQL source already has an import in progress')
        if sum(j['status'] in ('queued','running') for j in self.jobs.values())>=2:raise SqlTrendError('Two SQL imports are already running; wait for one to finish')
        s=SqlSettings.model_validate(source.settings);ranges={};signatures={}
        for meter in meters:
            signature=sql_stream_id(meter,source);signatures[meter.id]=signature
            if start is None:
                with self.store.lock:row=self.store.db.execute('SELECT timestamp FROM sql_watermarks WHERE meter_id=? AND stream_id=?',(meter.id,signature)).fetchone()
                begin=max(1,row[0]-s.overlap_hours*3600) if row else now-s.initial_days*86400
            else:begin=start
            if not 0<begin<end or end-begin>3661*86400:raise SqlTrendError('Choose an increasing historical range of at most ten years, ending no later than now')
            ranges[meter.id]={'start':begin,'end':end,'after':None,'done':False}
        jid=secrets.token_hex(10);job={'id':jid,'source_id':source_id,'meter_ids':meter_ids,'automatic':automatic,'status':'queued','created':now,'updated':now,
            'ranges':ranges,'signatures':signatures,'inserted':0,'duplicates':0,'quality_updates':0,'processed':0,'completed_meters':0,'current_meter':None,'cancel_requested':False,'error':None}
        self.jobs[jid]=job;self.save_job(job)
        for old in sorted(list(self.jobs.values()),key=lambda j:j['updated'])[:-20]:
            if old['status'] not in ('queued','running'):
                self.jobs.pop(old['id'],None)
                with self.store.lock,self.store.db:self.store.db.execute('DELETE FROM sql_jobs WHERE id=?',(old['id'],))
        self.tasks[jid]=asyncio.create_task(self.run_job(jid))
        self.store.audit('sql.import.start',{'job_id':jid,'source_id':source_id,'meter_count':len(meter_ids),'automatic':automatic})
        return self.public_job(job)
    def commit_page(self,job,meter,source,page):
        stream=sql_stream_id(meter,source)
        records=[Reading(meter_id=meter.id,stream_id=stream,**r) for r in page['readings']]
        if any(r.timestamp>time.time()+60 for r in records):raise SqlTrendError('SQL source returned a future timestamp')
        span=job['ranges'][meter.id]
        if any(not span['start']<=r.timestamp<span['end'] for r in records):raise SqlTrendError('SQL samples fall outside the selected range; check the timestamp mapping/timezone')
        if page['more'] and page['cursor']==span['after']:raise SqlTrendError('SQL paging cursor did not advance; import stopped')
        with self.store.lock,self.store.db:
            # Recheck after the network call so a concurrent source edit cannot append
            # values under a different mapping while a page is in flight.
            current_source,current_meters=self.selected(source.id,[meter.id])
            if sql_stream_id(current_meters[0],current_source)!=job['signatures'][meter.id]:raise SqlTrendError('The meter/source mapping changed during import; start a new job')
            inserted=duplicates=updates=0
            for r in records:
                old=self.store.db.execute('SELECT value,quality,error,stream_id FROM readings WHERE meter_id=? AND timestamp=?',(r.meter_id,r.timestamp)).fetchone()
                if old:
                    if old['value']!=r.value:raise SqlTrendError('A local reading differs from the SQL value at the same timestamp. Existing data was retained; review the conflict.')
                    duplicates+=1
                    if old['stream_id']==stream and (old['quality']!=r.quality or old['error']!=r.error):
                        self.store.db.execute('UPDATE readings SET quality=?,error=? WHERE meter_id=? AND timestamp=?',(r.quality,r.error,r.meter_id,r.timestamp));updates+=1
                else:
                    self.store.db.execute('INSERT INTO readings(meter_id,timestamp,value,quality,error,stream_id) VALUES(?,?,?,?,?,?)',(r.meter_id,r.timestamp,r.value,r.quality,r.error,r.stream_id));inserted+=1
            if records:
                latest=max(r.timestamp for r in records)
                self.store.db.execute('INSERT INTO sql_watermarks VALUES(?,?,?) ON CONFLICT(meter_id,stream_id) DO UPDATE SET timestamp=max(timestamp,excluded.timestamp)',(meter.id,stream,latest))
            job['inserted']+=inserted;job['duplicates']+=duplicates;job['quality_updates']+=updates;job['processed']+=len(records)
            job['ranges'][meter.id]['after']=page['cursor']
            job['ranges'][meter.id]['done']=not page['more']
            job['completed_meters']=sum(r['done'] for r in job['ranges'].values())
            job['updated']=time.time()
            self.store.db.execute('UPDATE sql_jobs SET updated=?,data=? WHERE id=?',(job['updated'],json.dumps(job,allow_nan=False),job['id']))
            latest=self.store.db.execute('SELECT quality,error,timestamp FROM readings WHERE meter_id=? ORDER BY timestamp DESC LIMIT 1',(meter.id,)).fetchone()
        if latest:self.status[meter.id]={'last_success':time.time(),'quality':latest['quality'],'error':latest['error']}
    async def run_job(self,jid):
        job=self.jobs[jid];job['status']='running';job['error']=None;self.save_job(job)
        try:
            for mid in job['meter_ids']:
                if job['ranges'][mid]['done']:continue
                job['current_meter']=mid
                while not job['ranges'][mid]['done']:
                    if self.stopping or job['cancel_requested']:
                        job['status']='interrupted' if self.stopping else 'cancelled';self.save_job(job);return
                    source,meters=self.selected(job['source_id'],[mid]);meter=meters[0]
                    if sql_stream_id(meter,source)!=job['signatures'][mid]:raise SqlTrendError('The source/meter mapping changed; start a new import')
                    span=job['ranges'][mid]
                    page=await self.read(source.id,'history_page',meter.point,span['start'],span['end'],span['after'])
                    if self.stopping or job['cancel_requested']:continue
                    self.commit_page(job,meter,source,page)
                    await asyncio.sleep(.15)
            job['status']='completed';job['current_meter']=None;self.save_job(job)
            self.store.audit('sql.import.complete',{'job_id':jid,'inserted':job['inserted'],'duplicates':job['duplicates'],'quality_updates':job['quality_updates']})
        except Exception as e:
            job['status']='failed';job['error']=str(e) if isinstance(e,SqlTrendError) else 'SQL import failed. Review the connection and mapping.'
            self.save_job(job)
            if job['current_meter']:self.status[job['current_meter']]={'last_error':time.time(),'quality':'offline','error':job['error']}
        finally:self.tasks.pop(jid,None)
    def cancel(self,jid):
        if jid not in self.jobs:raise SqlTrendError('SQL import job not found')
        job=self.jobs[jid]
        if job['status'] in ('running','queued'):job['cancel_requested']=True;self.save_job(job)
        return self.public_job(job)
    def resume(self,jid):
        if jid not in self.jobs:raise SqlTrendError('SQL import job not found')
        job=self.jobs[jid]
        if job['status'] not in ('failed','cancelled','interrupted'):raise SqlTrendError('Only a stopped import can be resumed')
        self.selected(job['source_id'],job['meter_ids'])
        if sum(j['status'] in ('queued','running') for j in self.jobs.values())>=2:raise SqlTrendError('Two SQL imports are already running; wait for one to finish')
        if any(j['source_id']==job['source_id'] and j['status'] in ('running','queued') for j in self.jobs.values()):raise SqlTrendError('Another import is running for this source')
        job['cancel_requested']=False;job['status']='queued';job['error']=None;self.save_job(job)
        self.tasks[jid]=asyncio.create_task(self.run_job(jid));return self.public_job(job)
    async def loop(self):
        while not self.stopping:
            for source in self.store.config().sources:
                if source.kind!='sql_server' or not source.enabled:continue
                settings=SqlSettings.model_validate(source.settings)
                mids=[m.id for m in self.store.config().meters if m.source_id==source.id and m.enabled]
                key='sql_attempt:'+source.id
                if not mids or time.time()-self.store.get(key,0)<settings.sync_seconds:continue
                if any(j['source_id']==source.id and j['status'] in ('queued','running') for j in self.jobs.values()):continue
                self.store.set(key,time.time())
                try:self.start(source.id,mids,automatic=True)
                except SqlTrendError:pass
            await asyncio.sleep(10)
    async def close(self):
        self.stopping=True
        if self.tasks:await asyncio.gather(*list(self.tasks.values()),return_exceptions=True)
        self.executor.shutdown(wait=True)
