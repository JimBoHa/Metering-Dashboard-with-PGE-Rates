import json
import time
from fastapi.testclient import TestClient
import pytest
from metering.web import create_app
from sql_fixture import SyntheticSql,settings

@pytest.fixture
def sql_session(tmp_path):
    db=SyntheticSql();app=create_app(tmp_path,background=False,sql_connector=db.connect)
    pwd=(tmp_path/'initial-access.txt').read_text().split('Password: ')[1].splitlines()[0]
    with TestClient(app) as client:
        login=client.post('/api/login',json={'password':pwd}).json();client.headers['X-CSRF-Token']=login['csrf']
        doc=client.get('/api/config').json();doc['configuration']['sources']=[{'id':'sql','name':'Synthetic SQL historian','kind':'sql_server','settings':settings(password='synthetic-sql-password')}]
        response=client.put('/api/config',json=doc);assert response.status_code==200,response.text
        yield client,app,db

def wait_job(client,jid,statuses=('completed','failed','cancelled')):
    for _ in range(200):
        job=client.get('/api/sql/jobs/'+jid).json()
        if job['status'] in statuses:return job
        time.sleep(.02)
    raise AssertionError('Job did not finish')

def add_sql_meter(client):
    point=client.get('/api/sql/sql/points').json()['points'][0]
    doc=client.get('/api/config').json()
    response=client.post('/api/sql/meters',json={'source_id':'sql','revision':doc['revision'],'start':'2026-11-01','end':'2026-11-05','meters':[{'id':'sql-meter','name':'Synthetic imported meter','unit':'kWh','source_id':'sql','point':point['point']}]})
    assert response.status_code==200,response.text
    return response.json()

def test_password_masked_encrypted_and_blank_preserved(sql_session):
    c,app,db=sql_session;doc=c.get('/api/config').json();s=doc['configuration']['sources'][0]
    assert s['settings']['password_set'] and 'password' not in s['settings']
    raw=app.state.store.get('configuration');assert 'synthetic-sql-password' not in json.dumps(raw)
    encrypted=app.state.store.db.execute('SELECT secret FROM sql_credentials').fetchone()[0]
    assert b'synthetic-sql-password' not in encrypted
    s['settings']['password']='';s['name']='Renamed synthetic source';assert c.put('/api/config',json=doc).status_code==200
    assert c.get('/api/sql/sql/inspect').status_code==200
    assert db.connect_calls[-1]['password']=='synthetic-sql-password'
    assert (app.state.store.directory/'sql-credentials.key').stat().st_mode & 0o777==0o600

def test_password_only_change_updates_revision(sql_session):
    c,app,db=sql_session;doc=c.get('/api/config').json();old=doc['revision'];doc['configuration']['sources'][0]['settings']['password']='another-synthetic-password'
    result=c.put('/api/config',json=doc).json();assert result['revision']!=old
    assert c.put('/api/config',json=doc).status_code==409

def test_import_keeps_times_and_is_idempotent(sql_session):
    c,app,db=sql_session;data=add_sql_meter(c);job=wait_job(c,data['job']['id']);assert job['status']=='completed',job
    assert job['inserted']==3
    rows=app.state.store.readings('sql-meter',1793491200,1793836800)
    assert len(rows)==3 and rows[0]['timestamp']==1793534400
    second=c.post('/api/sql/imports',json={'source_id':'sql','meter_ids':['sql-meter'],'start':'2026-11-01','end':'2026-11-05'}).json()
    duplicate=wait_job(c,second['id']);assert duplicate['inserted']==0 and duplicate['duplicates']==3

def test_schema_browse_and_global_search(sql_session):
    c,app,db=sql_session;assert c.get('/api/sql/sql/inspect').json()['profile']=='metasys'
    rows=c.get('/api/sql/sql/points?q=Water').json()['points'];assert len(rows)==1 and rows[0]['id']=='102'

def test_conflicting_local_data_is_not_overwritten(sql_session):
    c,app,db=sql_session;data=add_sql_meter(c);wait_job(c,data['job']['id'])
    with app.state.store.lock,app.state.store.db:app.state.store.db.execute("UPDATE readings SET value=999 WHERE meter_id='sql-meter'")
    next_job=c.post('/api/sql/imports',json={'source_id':'sql','start':'2026-11-01','end':'2026-11-05'}).json();result=wait_job(c,next_job['id'])
    assert result['status']=='failed' and 'differs' in result['error']
    assert app.state.store.latest()['sql-meter']['value']==999

def test_cancel_and_resume_from_checkpoint(sql_session):
    c,app,db=sql_session;db.count=121
    doc=c.get('/api/config').json();doc['configuration']['sources'][0]['settings']['batch_size']=50;c.put('/api/config',json=doc)
    data=add_sql_meter(c);jid=data['job']['id']
    for _ in range(100):
        if c.get('/api/sql/jobs/'+jid).json()['inserted']>=50:break
        time.sleep(.01)
    c.post('/api/sql/jobs/'+jid+'/cancel');stopped=wait_job(c,jid)
    assert stopped['status']=='cancelled';assert stopped['inserted']<121
    assert c.post('/api/sql/jobs/'+jid+'/resume').status_code==200
    done=wait_job(c,jid);assert done['status']=='completed' and done['inserted']==121

def test_validation_errors_never_echo_password(sql_session):
    c,app,db=sql_session;doc=c.get('/api/config').json();doc['configuration']['sources'][0]['settings']['password']='DO_NOT_ECHO_SYNTHETIC_SECRET'
    doc['configuration']['meters']=[{'id':'bad','name':'Bad','source_id':'missing'}]
    result=c.put('/api/config',json=doc)
    assert result.status_code==422;assert 'DO_NOT_ECHO_SYNTHETIC_SECRET' not in result.text

def test_sql_endpoints_require_login(tmp_path):
    app=create_app(tmp_path,background=False)
    with TestClient(app) as c:assert c.get('/api/sql/anything/inspect').status_code==401


def test_quality_policy_updates_identical_rows(sql_session):
    c,app,db=sql_session
    doc=c.get('/api/config').json();doc['configuration']['sources'][0]['settings']['missing_quality']='unverified';c.put('/api/config',json=doc)
    initial=add_sql_meter(c);wait_job(c,initial['job']['id'])
    assert app.state.store.latest()['sql-meter']['quality']=='unverified'
    doc=c.get('/api/config').json();doc['configuration']['sources'][0]['settings']['missing_quality']='good';c.put('/api/config',json=doc)
    job=c.post('/api/sql/imports',json={'source_id':'sql','start':'2026-11-01','end':'2026-11-05'}).json()
    done=wait_job(c,job['id']);assert done['quality_updates']==3
    assert app.state.store.latest()['sql-meter']['quality']=='good'


def test_checkpoint_and_encrypted_credentials_survive_restart(tmp_path):
    db=SyntheticSql(count=121)
    def opened():return create_app(tmp_path,background=False,sql_connector=db.connect)
    def logged(client,password):client.headers['X-CSRF-Token']=client.post('/api/login',json={'password':password}).json()['csrf']
    app=opened();pwd=(tmp_path/'initial-access.txt').read_text().split('Password: ')[1].splitlines()[0]
    with TestClient(app) as c:
        logged(c,pwd);doc=c.get('/api/config').json();doc['configuration']['sources']=[{'id':'sql','name':'Synthetic SQL','kind':'sql_server','settings':settings(password='synthetic-sql-password',batch_size=50)}];c.put('/api/config',json=doc)
        data=add_sql_meter(c);jid=data['job']['id']
        for _ in range(100):
            if c.get('/api/sql/jobs/'+jid).json()['inserted']>=50:break
            time.sleep(.01)
        c.post('/api/sql/jobs/'+jid+'/cancel');assert wait_job(c,jid)['status']=='cancelled'
    app=opened()
    with TestClient(app) as c:
        logged(c,pwd);assert c.get('/api/sql/jobs/'+jid).json()['status']=='cancelled'
        assert c.post('/api/sql/jobs/'+jid+'/resume').status_code==200
        done=wait_job(c,jid);assert done['status']=='completed' and done['inserted']==121
        assert app.state.store.db.execute("SELECT count(*) FROM readings WHERE meter_id='sql-meter'").fetchone()[0]==121
