"""Synthetic live totals, measurement dimensions and billing-quality separation."""
from datetime import date
import pytest
from fastapi.testclient import TestClient
from metering.billing import Accounting,local_midnight
from metering.live import LiveView,live_units
from metering.acquisition import stream_fingerprint
from metering.models import Configuration,Meter,Reading,Source
from metering.store import Store
from metering.web import create_app


@pytest.fixture
def store(tmp_path):
    s=Store(tmp_path);yield s;s.close()


def configure(store,meters):
    cfg=Configuration(meters=meters);store.set('configuration',cfg.model_dump());return cfg


def add(store,mid,stamp,value,quality='good',stream='synthetic'):
    store.add_reading(Reading(meter_id=mid,timestamp=stamp,value=value,quality=quality,stream_id=stream))


def test_three_live_power_inputs_sum_kw_without_summing_energy_registers(store):
    meters=[Meter(id=str(i),name='Synthetic feeder '+str(i),mode='rate') for i in range(3)]
    total=Meter(id='total',name='Synthetic total power',mode='virtual',terms=[{'meter_id':str(i)} for i in range(3)],display_on_dashboard='live')
    configure(store,meters+[total])
    for i,value in enumerate((100,200,50)):add(store,str(i),1000+i,value)
    result=LiveView(store,now=1005).reading('total')
    assert result['value']==350 and result['unit']=='kW' and result['kind']=='rate'
    assert result['timestamp']==1000 and result['quality']=='good'


def test_live_missing_or_stale_input_is_not_treated_as_zero(store):
    configure(store,[Meter(id='a',name='A',mode='rate',max_gap_seconds=60),Meter(id='b',name='B',mode='rate',max_gap_seconds=60),Meter(id='total',name='Total',mode='virtual',terms=[{'meter_id':'a'},{'meter_id':'b'}])])
    add(store,'a',1000,100);add(store,'b',900,50)
    result=LiveView(store,now=1005).reading('total')
    assert result['value'] is None and result['partial_value']==100
    assert result['available_inputs']==1 and result['required_inputs']==2


def test_counter_and_power_can_share_usage_unit_but_not_a_live_sum(store):
    configure(store,[Meter(id='a',name='Power',mode='rate'),Meter(id='b',name='Counter'),Meter(id='total',name='Mixed',mode='virtual',terms=[{'meter_id':'a'},{'meter_id':'b'}])])
    add(store,'a',1000,100);add(store,'b',1000,100000)
    result=LiveView(store,now=1005).reading('total')
    assert result['value'] is None and result['quality']=='incompatible'


def test_signed_flow_live_units_convert_before_subtraction(store):
    configure(store,[Meter(id='s',name='Supply',unit='gal',mode='rate',rate_period_seconds=60),Meter(id='r',name='Return',unit='gal',mode='rate',rate_period_seconds=3600),Meter(id='n',name='Net',unit='gal',mode='virtual',terms=[{'meter_id':'s'},{'meter_id':'r','factor':-1}])])
    add(store,'s',1000,10);add(store,'r',1000,120)
    live=LiveView(store,now=1005).reading('n')
    assert live['value']==8 and live['unit']=='gal/min'


def test_monthly_counter_total_is_consumption_not_lifetime_register_sum(store):
    start=local_midnight(date(2026,11,1),'America/Los_Angeles')
    configure(store,[Meter(id='a',name='A',max_gap_seconds=3600),Meter(id='b',name='B',max_gap_seconds=3600),Meter(id='total',name='Total',mode='virtual',terms=[{'meter_id':'a'},{'meter_id':'b'}],display_on_dashboard='month_to_date')])
    add(store,'a',start,100000);add(store,'a',start+3600,100010)
    add(store,'b',start,500000);add(store,'b',start+3600,500020)
    summary=LiveView(store,now=start+3600).summary()
    assert summary['readings']['total']['value']==600030
    assert summary['month_to_date']['total']['quantity']==30
    assert summary['month_to_date']['total']['coverage']==1
    assert summary['month_to_date']['total']['verified_quantity']==30


def test_unverified_operational_preview_never_becomes_billable_usage(store):
    start=local_midnight(date(2026,11,1),'America/Los_Angeles')
    configure(store,[Meter(id='a',name='Unverified input',display_on_dashboard='month_to_date')])
    add(store,'a',start,100,'unverified');add(store,'a',start+600,110,'unverified')
    summary=LiveView(store,now=start+600).summary()
    observed=summary['month_to_date']['a']
    assert observed['quantity']==10 and observed['verified_quantity'] is None
    assert observed['provisional'] and summary['readings']['a']['quality']=='unverified'
    assert Accounting(store).usage('a',start,start+600)['quantity']==0


def test_monthly_formula_uses_common_history_and_excludes_faulted_samples(store):
    start=local_midnight(date(2026,11,1),'America/Los_Angeles')
    configure(store,[Meter(id='a',name='A'),Meter(id='b',name='B'),Meter(id='total',name='Total',mode='virtual',terms=[{'meter_id':'a'},{'meter_id':'b'}],display_on_dashboard='month_to_date')])
    add(store,'a',start,100);add(store,'a',start+600,110);add(store,'a',start+1200,120)
    add(store,'b',start+600,200);add(store,'b',start+1200,210)
    m=LiveView(store,now=start+1200).summary()['month_to_date']['total']
    assert m['quantity']==20 and m['coverage']==.5 and m['first_interval']==start+600
    with store.lock,store.db:store.db.execute("UPDATE readings SET quality='fault' WHERE meter_id='b'")
    assert LiveView(store,now=start+1200).summary()['month_to_date']['total']['quantity'] is None


def test_collector_offline_status_overrides_fresh_old_value(store):
    configure(store,[Meter(id='a',name='A')]);add(store,'a',1000,100)
    r=LiveView(store,now=1005,status={'a':{'quality':'offline','error':'Synthetic unavailable source'}}).reading('a')
    assert r['value'] is None and r['quality']=='offline'


def test_live_remapping_waits_for_new_source_reading_but_renaming_preserves_it(store):
    source=Source(id='http',name='Synthetic reader',kind='http_json',settings={'url':'https://example.invalid/readings'})
    meter=Meter(id='a',name='Original label',source_id='http',point={'path':'old'})
    cfg=Configuration(sources=[source],meters=[meter]);store.set('configuration',cfg.model_dump())
    # The collector fingerprints the validated, persisted configuration.
    cfg=store.config();meter=cfg.meters[0];source=cfg.sources[0]
    add(store,'a',1000,100,stream=stream_fingerprint(meter,source))
    cfg.meters[0].name='Renamed';cfg.meters[0].display_on_dashboard='live';store.set('configuration',cfg.model_dump())
    assert LiveView(store,now=1005).reading('a')['value']==100
    cfg.meters[0].point={'path':'new'};store.set('configuration',cfg.model_dump())
    assert LiveView(store,now=1005).reading('a')['value'] is None


def test_live_endpoint_and_pin_configuration(tmp_path):
    with TestClient(create_app(tmp_path,background=False,auth_required=False)) as client:
        client.headers['X-CSRF-Token']=client.get('/api/session').json()['csrf']
        doc=client.get('/api/config').json()
        doc['configuration']['meters']=[{'id':'demo','name':'Synthetic pinned power','mode':'rate','display_on_dashboard':'live'}]
        assert client.put('/api/config',json=doc).status_code==200
        response=client.get('/api/live')
        assert response.status_code==200 and response.json()['readings']['demo']['unit']=='kW'
        assert response.json()['month_to_date']['demo']['quantity'] is None
        doc=client.get('/api/config').json();doc['configuration']['meters'][0]['display_on_dashboard']='invalid'
        assert client.put('/api/config',json=doc).status_code==422
