"""All prices, names and readings in these tests are synthetic."""
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
import pytest
from metering.store import Store
from metering.models import Configuration, Meter, Reading, Expense, RateSelection
from metering.billing import Accounting, local_midnight

@pytest.fixture
def store(tmp_path):
    s=Store(tmp_path)
    yield s
    s.close()

def configure(store,meters,**kwargs):
    c=Configuration(meters=meters,**kwargs);store.set('configuration',c.model_dump());return c

def add(store,mid,t,v,quality='good'):
    store.add_reading(Reading(meter_id=mid,timestamp=t,value=v,quality=quality))

def test_counter_clip_uses_boundary_samples(store):
    configure(store,[Meter(id='a',name='Counter')]);add(store,'a',1000,100);add(store,'a',1600,160)
    u=Accounting(store).usage('a',1100,1500)
    assert u['quantity']==pytest.approx(40);assert u['coverage']==1

def test_reset_not_charged(store):
    configure(store,[Meter(id='a',name='Counter')]);add(store,'a',1000,100);add(store,'a',1600,5)
    u=Accounting(store).usage('a',1000,1600)
    assert u['quantity']==0;assert 'Counter reset excluded' in u['issues']

def test_explicit_rollover(store):
    configure(store,[Meter(id='a',name='Counter',rollover=100)]);add(store,'a',1000,95);add(store,'a',1600,5)
    assert Accounting(store).usage('a',1000,1600)['quantity']==10

@pytest.mark.parametrize('quality',['unverified','fault','offline'])
def test_non_good_samples_excluded(store,quality):
    configure(store,[Meter(id='a',name='Counter')]);add(store,'a',1000,100);add(store,'a',1600,160,quality)
    assert Accounting(store).usage('a',1000,1600)['quantity']==0

def test_gaps_not_bridged(store):
    configure(store,[Meter(id='a',name='Counter',max_gap_seconds=100)]);add(store,'a',1000,100);add(store,'a',1600,160)
    assert Accounting(store).usage('a',1000,1600)['coverage']==0

def test_trapezoid_rate_integration(store):
    configure(store,[Meter(id='a',name='Power',mode='rate',max_gap_seconds=4000)]);add(store,'a',3600,2);add(store,'a',7200,4)
    assert Accounting(store).usage('a',3600,7200)['quantity']==pytest.approx(3)

def test_flow_per_minute(store):
    configure(store,[Meter(id='a',name='Flow',unit='gal',mode='rate',rate_period_seconds=60)]);add(store,'a',1000,10);add(store,'a',1600,10)
    assert Accounting(store).usage('a',1000,1600)['quantity']==100

def test_supply_return_only_common_coverage(store):
    configure(store,[Meter(id='s',name='Supply',unit='gal'),Meter(id='r',name='Return',unit='gal'),Meter(id='n',name='Net',unit='gal',mode='virtual',terms=[{'meter_id':'s','factor':1},{'meter_id':'r','factor':-1}])])
    add(store,'s',1000,100);add(store,'s',1600,160);add(store,'r',1200,10);add(store,'r',1600,20)
    u=Accounting(store).usage('n',1000,1600)
    assert u['quantity']==pytest.approx(30);assert u['coverage']==pytest.approx(2/3)

def test_formula_cycles_and_units_rejected():
    with pytest.raises(ValueError):Configuration(meters=[Meter(id='a',name='A',mode='virtual',terms=[{'meter_id':'a'}])])
    with pytest.raises(ValueError):Configuration(meters=[Meter(id='a',name='A'),Meter(id='b',name='B',unit='gal',mode='virtual',terms=[{'meter_id':'a'}])])

def test_interval_reads_deduplicate(store):
    configure(store,[Meter(id='a',name='Interval',mode='interval',interval_seconds=600)])
    add(store,'a',1600,10);add(store,'a',1600,10)
    assert Accounting(store).usage('a',1000,1600)['quantity']==10

@pytest.mark.parametrize('day,hours',[('2026-03-08',23),('2026-11-01',25)])
def test_dst_billing_day_length(day,hours):
    start=date.fromisoformat(day)
    assert local_midnight(start+timedelta(days=1),'America/Los_Angeles')-local_midnight(start,'America/Los_Angeles')==hours*3600

def synthetic_tariff(store,start='2026-01-01'):
    selection=RateSelection(schedule='B-19').model_dump()
    tariff={'id':'synthetic-test','selection':selection,'effective_from':start,'effective_to':None,'energy':{'summer':{'off_peak':.1,'peak':.5,'partial_peak':.3},'winter':{'off_peak':.1,'peak':.5,'super_off_peak':.05}},'demand':[],'customer_daily':0,'fetched_at':1}
    store.save_tariff(tariff);return tariff

def test_tou_split_and_markup(store):
    configure(store,[Meter(id='a',name='Electrical',billable=True,max_gap_seconds=7200)],rate=RateSelection(schedule='B-19'),markup_percent=10)
    t=datetime(2026,7,1,15,30,tzinfo=ZoneInfo('America/Los_Angeles')).timestamp()
    add(store,'a',t,100);add(store,'a',t+3600,110);synthetic_tariff(store)
    r=Accounting(store).report(t,t+3600);row=r['meters'][0]
    assert row['electricity']['energy_cost']==pytest.approx(4)
    assert row['markup']==.4;assert r['total_cost']==4.4

def test_unpriced_usage_never_uses_zero_tariff(store):
    configure(store,[Meter(id='a',name='Electrical',billable=True)],rate=RateSelection(schedule='B-19'))
    add(store,'a',1000,0);add(store,'a',1600,10)
    r=Accounting(store).report(1000,1600)
    assert r['meters'][0]['electricity']['unpriced_quantity']==10;assert not r['complete']

def expense(store,weights=(1,1,1),amount=10):
    start=local_midnight(date(2026,1,1),'America/Los_Angeles');end=start+86400
    configure(store,[Meter(id=str(i),name='Synthetic '+str(i),unit='gal',max_gap_seconds=86400) for i in range(3)])
    for i in range(3):add(store,str(i),start,0);add(store,str(i),end,i+1)
    return Expense(id='invoice',description='Synthetic water bill',billed_on=date(2026,1,2),period_start=date(2026,1,1),period_end=date(2026,1,2),amount=amount,allocations=[{'meter_id':str(i),'weight':w} for i,w in enumerate(weights)])

def test_allocations_balance_to_cent(store):
    e=expense(store);a=Accounting(store).allocate_expense(e)
    assert a['allocated'];assert sum(round(r['base_cost']*100) for r in a['rows'])==1000
    assert [r['base_cost'] for r in a['rows']]==[1.67,3.33,5]

def test_weighted_allocation(store):
    e=expense(store,weights=(3,1,1));a=Accounting(store).allocate_expense(e)
    assert a['rows'][0]['base_cost']==3.75;assert a['rows'][2]['base_cost']==3.75

def test_missing_expense_usage_blocks_allocation(store):
    e=expense(store)
    with store.lock,store.db:store.db.execute('DELETE FROM readings WHERE meter_id=?',('1',))
    assert not Accounting(store).allocate_expense(e)['allocated']

def test_daily_fixed_and_monthly_demand(store):
    start=local_midnight(date(2026,2,1),'America/Los_Angeles');end=local_midnight(date(2026,3,1),'America/Los_Angeles')
    configure(store,[Meter(id='a',name='Synthetic demand',mode='rate',billable=True,electricity_charges='account',max_gap_seconds=900)],rate=RateSelection(schedule='B-19'))
    for t in range(int(start),int(end)+1,900):add(store,'a',t,4)
    tariff=synthetic_tariff(store);tariff['id']='synthetic-demand';tariff['fetched_at']=2;tariff['customer_daily']=1;tariff['demand']=[{'season':'winter','period':'maximum','basis':'monthly','price':2}];store.save_tariff(tariff)
    r=Accounting(store).electricity('a',start,end,store.tariffs())
    assert r['demand_cost']==8;assert r['fixed_cost']==28


def test_submeters_do_not_duplicate_account_fees(store):
    start=local_midnight(date(2026,2,1),'America/Los_Angeles');end=local_midnight(date(2026,3,1),'America/Los_Angeles')
    configure(store,[Meter(id='a',name='Synthetic tenant',mode='rate',billable=True,max_gap_seconds=900)],rate=RateSelection(schedule='B-19'))
    for t in range(int(start),int(end)+1,900):add(store,'a',t,4)
    tariff=synthetic_tariff(store);tariff['id']='synthetic-fees';tariff['fetched_at']=2;tariff['customer_daily']=1;tariff['demand']=[{'season':'winter','period':'maximum','basis':'monthly','price':2}];store.save_tariff(tariff)
    r=Accounting(store).electricity('a',start,end,store.tariffs())
    assert r['demand_cost']==0;assert r['fixed_cost']==0;assert r['energy_cost']>0


def test_source_remapping_cannot_create_a_counter_jump(store):
    configure(store,[Meter(id='a',name='Reconfigured input')])
    store.add_reading(Reading(meter_id='a',timestamp=1000,value=10,stream_id='old-device'))
    store.add_reading(Reading(meter_id='a',timestamp=1600,value=100000,stream_id='new-device'))
    usage=Accounting(store).usage('a',1000,1600)
    assert usage['quantity']==0
    assert 'Source configuration changed; transition excluded' in usage['issues']


def test_pge_price_clock_stays_pacific_with_utc_workspace(store):
    configure(store,[Meter(id='a',name='Synthetic electrical input',billable=True,max_gap_seconds=7200)],timezone='UTC',rate=RateSelection(schedule='B-19'))
    t=datetime(2026,7,1,15,30,tzinfo=ZoneInfo('America/Los_Angeles')).timestamp()
    add(store,'a',t,100);add(store,'a',t+3600,110);synthetic_tariff(store)
    assert Accounting(store).electricity('a',t,t+3600,store.tariffs())['energy_cost']==pytest.approx(4)
