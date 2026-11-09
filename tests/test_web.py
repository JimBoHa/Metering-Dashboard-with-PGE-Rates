from fastapi.testclient import TestClient
import pytest
from metering.web import create_app

@pytest.fixture
def session(tmp_path):
    app=create_app(tmp_path,background=False)
    password=(tmp_path/'initial-access.txt').read_text().split('Password: ')[1].split('\n')[0]
    with TestClient(app) as client:
        result=client.post('/api/login',json={'password':password})
        assert result.status_code==200
        client.headers['X-CSRF-Token']=result.json()['csrf']
        yield client,app,password

def test_auth_and_csrf(tmp_path):
    app=create_app(tmp_path,background=False)
    with TestClient(app) as c:
        assert c.get('/api/config').status_code==401
        assert c.get('/api/health').status_code==200
        assert c.post('/api/login',json={'password':'wrong'}).status_code==401

def test_config_concurrency_and_validation(session):
    c,app,p=session
    doc=c.get('/api/config').json();doc['configuration']['markup_percent']=12
    assert c.put('/api/config',json=doc).status_code==200
    assert c.put('/api/config',json=doc).status_code==409
    doc=c.get('/api/config').json();doc['configuration']['markup_percent']=-10
    assert c.put('/api/config',json=doc).status_code==422

def test_mutations_need_csrf_and_same_origin(session):
    c,app,p=session
    doc=c.get('/api/config').json()
    assert c.put('/api/config',json=doc,headers={'X-CSRF-Token':'wrong'}).status_code==403
    assert c.put('/api/config',json=doc,headers={'Origin':'https://untrusted.invalid'}).status_code==403

def test_double_electric_billing_rejected(session):
    c,app,p=session;doc=c.get('/api/config').json()
    doc['configuration']['meters']=[{'id':'input','name':'Input','billable':True},{'id':'net','name':'Net','mode':'virtual','terms':[{'meter_id':'input'}],'billable':True}]
    assert c.put('/api/config',json=doc).status_code==400

def test_import_export_and_report(session):
    c,app,p=session;doc=c.get('/api/config').json();doc['configuration']['meters']=[{'id':'a','name':'=SYNTHETIC','unit':'gal','max_gap_seconds':86400}]
    assert c.put('/api/config',json=doc).status_code==200
    from datetime import date
    from metering.billing import local_midnight
    t=local_midnight(date(2026,1,1),'America/Los_Angeles')
    assert c.post('/api/readings',json=[{'meter_id':'a','timestamp':t,'value':0},{'meter_id':'a','timestamp':t+86400,'value':60}]).status_code==200
    r=c.get('/api/report?start=2026-01-01&end=2026-01-02').json()
    assert r['meters'][0]['quantity']==60
    assert "'=SYNTHETIC" in c.get('/api/report.csv?start=2026-01-01&end=2026-01-02').text
    assert c.get('/api/report?start=bad&end=2026-01-02').status_code==400

def test_expense_parent_child_overlap_rejected(session):
    c,app,p=session;doc=c.get('/api/config').json();doc['configuration']['meters']=[{'id':'a','name':'Input','unit':'gal'},{'id':'n','name':'Net','unit':'gal','mode':'virtual','terms':[{'meter_id':'a'}]}]
    c.put('/api/config',json=doc)
    e={'id':'bill','description':'Synthetic','billed_on':'2026-01-02','period_start':'2026-01-01','period_end':'2026-01-02','amount':10,'allocations':[{'meter_id':'a'},{'meter_id':'n'}]}
    assert c.put('/api/expenses/bill',json=e).status_code==400

def test_password_change_revokes_sessions(session):
    c,app,password=session
    assert c.post('/api/password',json={'current_password':password,'new_password':'a-new-synthetic-password'}).status_code==200
    assert c.get('/api/config').status_code==401
    assert not (app.state.store.directory/'initial-access.txt').exists()
