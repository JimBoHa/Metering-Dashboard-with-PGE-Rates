"""Rate tests generate arbitrary synthetic workbook data, never PG&E prices."""
from datetime import datetime
from zoneinfo import ZoneInfo
from io import BytesIO
import openpyxl
import pytest
from metering.rates import catalog, parse_workbook, period_at, safe_url, normalize_schedule
from metering.models import RateSelection

@pytest.mark.parametrize('schedule,hour,month,wanted',[
 ('B-19',16,7,('summer','peak')),('B-19',21,7,('summer','partial_peak')),('B-20',14,7,('summer','partial_peak')),('B-6',14,7,('summer','off_peak')),('B-20',9,3,('winter','super_off_peak')),('B-20',9,2,('winter','off_peak')),('B-19',23,7,('summer','off_peak')),('B-19',14,3,('winter','off_peak'))])
def test_official_time_windows(schedule,hour,month,wanted):
    # Includes a weekend; the supported schedules apply every day.
    stamp=datetime(2026,month,1,hour,tzinfo=ZoneInfo('America/Los_Angeles')).timestamp()
    assert period_at(stamp,schedule)==wanted

def test_index_discovery_and_inclusive_end_date():
    html='<a href="https://view.officeapps.live.com/op/view.aspx?src=https%3A%2F%2Fwww.pge.com%2Fassets%2Fsynthetic.xlsx">Commercial (B) (JAN 1, 2026 - FEB 28, 2026)</a>'
    entry=catalog(html)[0]
    assert entry['effective_to']=='2026-03-01';assert entry['url'].endswith('/synthetic.xlsx')

@pytest.mark.parametrize('url',['http://www.pge.com/a.xlsx','https://pge.com.attacker.invalid/a.xlsx','https://user@www.pge.com/a.xlsx','https://www.pge.com:444/a.xlsx'])
def test_download_host_allowlist(url):
    with pytest.raises(ValueError):safe_url(url)

def test_no_substitute_schedules():
    assert normalize_schedule('b 19')=='B-19'
    with pytest.raises(ValueError):normalize_schedule('B-190')

def workbook():
    w=openpyxl.Workbook();s=w.active;s.title='Industrial_B-20_260101-Present'
    s['A2']='B20 Secondary Firm';s['B2']=1.23
    synthetic=[('Summer','Max. Peak','Peak'),(None,'Max. Part-Peak','Part-Peak'),(None,'Maximum','Off-Peak'),('Winter','Max. Peak','Peak'),(None,None,'Off-Peak'),(None,'Maximum','Super Off-Peak')]
    for i,(season,demand,energy) in enumerate(synthetic,2):
        s.cell(i,3,season);s.cell(i,4,demand);s.cell(i,5,2 if demand else None);s.cell(i,6,energy);s.cell(i,7,.1+i*.01)
    out=BytesIO();w.save(out);return out.getvalue()

def test_workbook_component_parsing():
    entry={'effective_from':'2026-01-01','effective_to':None,'url':'https://www.pge.com/synthetic.xlsx','label':'Synthetic test only'}
    d=parse_workbook(workbook(),RateSelection(schedule='B-20'),entry)
    assert len(d['demand'])==5;assert d['energy']['summer']['partial_peak']==pytest.approx(.13)
    assert d['customer_daily']==1.23;assert d['cells']

def test_delivery_never_uses_bundled_rates():
    with pytest.raises(ValueError,match='Delivery-only'):
        parse_workbook(workbook(),RateSelection(schedule='B-20',service='delivery'),{})


def test_successful_refresh_records_daily_check_timestamp(tmp_path,monkeypatch):
    import asyncio
    from metering import rates
    from metering.store import Store
    import time
    async def downloaded(url):
        if url==rates.INDEX:
            return b'<a href="https://www.pge.com/synthetic.xlsx">Industrial_B-20 (JAN 1, 2026 - Present)</a>'
        return workbook()
    monkeypatch.setattr(rates,'download',downloaded)
    store=Store(tmp_path)
    asyncio.run(rates.fetch_tariff(store,RateSelection(schedule='B-20')))
    sync=store.get('rate_sync')
    assert time.time()-sync['last_attempt']<5
    assert sync['last_success']>0
    store.close()


def test_workbook_and_index_effective_dates_must_match():
    entry={'effective_from':'2026-03-01','effective_to':None,'url':'https://www.pge.com/synthetic.xlsx','label':'Synthetic test only'}
    with pytest.raises(ValueError,match='effective dates disagree'):
        parse_workbook(workbook(),RateSelection(schedule='B-20'),entry)
