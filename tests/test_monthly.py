"""Synthetic prices and readings only; no customer or PG&E price tables."""
import asyncio
import csv
import io
import time
from datetime import date, datetime
import openpyxl
import pytest
from fastapi.testclient import TestClient
from metering.billing import Accounting, applicable_tariff, local_midnight, money
from metering.models import Configuration, Meter, MonthlyRate, RateSelection, Expense
from metering.periods import PGE_ZONE, months_between
from metering.store import Store
from metering.web import create_app
from metering import rates


def selection(schedule="B-19"):
    return RateSelection(schedule=schedule)


def tariff(store,identity,start,price,end=None,schedule="B-19",demand=0,daily=0):
    data={"id":identity,"fetched_at":time.time(),"selection":selection(schedule).model_dump(),"effective_from":start,"effective_to":end,
          "energy":{season:{p:price for p in ("peak","partial_peak","off_peak","super_off_peak")} for season in ("summer","winter")},
          "demand":[{"season":season,"period":"maximum","basis":"monthly","price":demand} for season in ("summer","winter")] if demand else [],"customer_daily":daily}
    store.save_tariff(data);return data


def configure(store,assignments,account=False,zone=PGE_ZONE):
    cfg=Configuration(timezone=zone,rate=selection("B-6"),markup_percent=10,
        meters=[Meter(id="electric",name="Synthetic electrical input",mode="rate",billable=True,electricity_charges="account" if account else "energy_only",max_gap_seconds=3600)],
        monthly_rates=[MonthlyRate(month=m,selection=selection(s)) for m,s in assignments])
    store.set("configuration",cfg.model_dump());return cfg


def fill(store,start,end,value):
    a,b=local_midnight(start,PGE_ZONE),local_midnight(end,PGE_ZONE)
    with store.lock,store.db:
        store.db.executemany("INSERT INTO readings(meter_id,timestamp,value,quality) VALUES('electric',?,?,'good')",
            [(t,value(t) if callable(value) else value) for t in range(int(a),int(b)+1,900)])
    return a,b


@pytest.fixture
def store(tmp_path):
    s=Store(tmp_path);yield s;s.close()


def test_twelve_month_history_uses_each_month_schedule_and_effective_prices(store):
    periods=months_between("2025-11","2026-11")
    assignments=[(a.strftime("%Y-%m"),"B-19" if a<date(2026,5,1) else "B-20") for a,b in periods]
    configure(store,assignments,zone="UTC")
    fill(store,date(2025,11,1),date(2026,11,1),2)
    tariff(store,"former","2025-01-01",.1,end="2026-03-01")
    tariff(store,"later","2026-03-01",.2)
    tariff(store,"other-schedule","2025-01-01",.3,schedule="B-20")
    tariff(store,"current-default-must-not-be-used","2025-01-01",9,schedule="B-6")
    result=Accounting(store).monthly_report("2025-11","2026-11")
    assert result["complete"] and result["complete_months"]==12 and result["timezone"]==PGE_ZONE
    for (a,b),month in zip(periods,result["months"]):
        expected_kwh=2*(local_midnight(b,PGE_ZONE)-local_midnight(a,PGE_ZONE))/3600
        price=.1 if a<date(2026,3,1) else .2 if a<date(2026,5,1) else .3
        assert month["electricity_kwh"]==pytest.approx(expected_kwh)
        assert month["energy_cost"]==money(expected_kwh*price)
        assert month["total_cost"]==money(money(expected_kwh*price)+money(expected_kwh*price*.1))
        assert month["rate_coverage"]["complete"]
    assert result["totals"]["total_cost"]==money(sum(m["total_cost"] for m in result["months"]))


def test_monthly_demand_and_fixed_charges_reset_per_month(store):
    configure(store,[("2026-01","B-19"),("2026-02","B-19")],account=True)
    boundary=local_midnight(date(2026,2,1),PGE_ZONE)
    # The boundary sample belongs to both trapezoids; use interval quantities for exact step changes.
    cfg=store.config();cfg.meters[0].mode="interval";cfg.meters[0].interval_seconds=900;store.set("configuration",cfg.model_dump())
    a,b=fill(store,date(2026,1,1),date(2026,3,1),lambda t:1 if t<=boundary else .5)
    tariff(store,"synthetic-demand","2025-01-01",.1,demand=2,daily=1)
    result=Accounting(store).monthly_report("2026-01","2026-03")
    assert result["complete"]
    assert [m["demand_cost"] for m in result["months"]]==[8,4]
    assert [m["fixed_cost"] for m in result["months"]]==[31,28]
    assert result["totals"]["demand_cost"]==12 and result["totals"]["fixed_cost"]==59


def test_missing_month_assignment_does_not_reuse_current_selection(store):
    configure(store,[("2026-01","B-19")]);fill(store,date(2026,1,1),date(2026,3,1),1)
    tariff(store,"b19","2025-01-01",.1);tariff(store,"b6","2025-01-01",.9,schedule="B-6")
    result=Accounting(store).monthly_report("2026-01","2026-03")
    feb=result["months"][1]
    assert result["complete_months"]==1 and not result["complete"]
    assert feb["selection"] is None and feb["energy_cost"]==0 and feb["unpriced_quantity"]==672
    assert any("current schedule is not substituted" in n for n in feb["notes"])


def test_month_with_two_tariff_versions_splits_energy_and_flags_account_reconciliation(store):
    configure(store,[("2026-02","B-19")],account=True);fill(store,date(2026,2,1),date(2026,3,1),1)
    tariff(store,"first","2026-02-01",.1,end="2026-02-15",demand=2,daily=1)
    tariff(store,"second","2026-02-15",.2,demand=3,daily=2)
    month=Accounting(store).monthly_report("2026-02","2026-03")["months"][0]
    assert month["energy_cost"]==100.8
    assert not month["complete"] and not month["meters"][0]["electricity"]["account_complete"]
    assert month["demand_cost"]==0 and month["fixed_cost"]==0


def test_latest_expired_version_does_not_fall_back_to_stale_present(store):
    old=tariff(store,"stale","2025-01-01",.1)
    new=tariff(store,"closed","2026-01-01",.2,end="2026-02-01")
    assert applicable_tariff("2026-02-01",selection().model_dump(),[old,new]) is None


def test_billed_components_reconcile_to_monthly_base_cost(store):
    configure(store,[("2026-02","B-19")],account=True);fill(store,date(2026,2,1),date(2026,3,1),1)
    tariff(store,"synthetic-rounding","2025-01-01",.00001,demand=.005,daily=.0002)
    month=Accounting(store).monthly_report("2026-02","2026-03")["months"][0]
    assert month["complete"]
    assert month["energy_cost"]==.01 and month["demand_cost"]==.01 and month["fixed_cost"]==.01
    assert month["base_cost"]==.03 and month["total_cost"]==.03


def test_monthly_shared_bill_uses_historical_usage_and_monthly_markup(store):
    cfg=Configuration(markup_percent=10,meters=[Meter(id="water",name="Synthetic water",unit="gal",mode="interval",interval_seconds=86400)])
    store.set("configuration",cfg.model_dump())
    start=local_midnight(date(2026,2,1),PGE_ZONE)
    with store.lock,store.db:
        store.db.executemany("INSERT INTO readings(meter_id,timestamp,value,quality) VALUES('water',?,10,'good')",[(start+i*86400,) for i in range(1,29)])
        expense=Expense(id="water-bill",description="Synthetic water expense",billed_on=date(2026,3,3),period_start=date(2026,2,1),period_end=date(2026,3,1),amount=12.34,allocations=[{"meter_id":"water"}])
        store.db.execute("INSERT INTO expenses VALUES(?,?)",(expense.id,expense.model_dump_json()))
    result=Accounting(store).monthly_report("2026-02","2026-03")
    month=result["months"][0]
    assert result["complete"] and month["expense_cost"]==12.34
    assert month["markup"]==1.23 and month["total_cost"]==13.57
    assert month["meters"][0]["quantity"]==280


@pytest.mark.parametrize("start,end",[("2026-13","2026-14"),("2026-2","2026-03"),("2026-01","2026-01"),("2026-02","2026-01"),("2020-01","2024-01")])
def test_invalid_monthly_ranges_rejected(start,end):
    with pytest.raises(ValueError):months_between(start,end)


def test_plan_validation_normalizes_and_rejects_duplicates():
    monthly=MonthlyRate(month="2026-02",selection=RateSelection(schedule="b 19"))
    assert monthly.selection.schedule=="B-19"
    with pytest.raises(ValueError):Configuration(monthly_rates=[monthly,monthly])
    with pytest.raises(ValueError):MonthlyRate(month="2026-13",selection=selection())
    with pytest.raises(ValueError):MonthlyRate(month="2026-02",selection=selection("unknown"))


def workbook_for(effective):
    wb=openpyxl.Workbook();sheet=wb.active;sheet.title="Industrial_B-20_"+effective.replace("-","")[2:]+"-Present"
    sheet.append(["B20 Secondary Firm",1.23,"Summer","Max. Peak",2,"Peak",.12])
    for row in [(None,None,None,"Max. Part-Peak",2,"Part-Peak",.13),(None,None,None,"Maximum",2,"Off-Peak",.14),(None,None,"Winter","Max. Peak",2,"Peak",.15),(None,None,None,None,None,"Off-Peak",.16),(None,None,None,"Maximum",2,"Super Off-Peak",.17)]:sheet.append(row)
    out=io.BytesIO();wb.save(out);wb.close();return out.getvalue()


def synthetic_download(monkeypatch,fail_second=False):
    calls=[]
    async def download(url):
        calls.append(url)
        if url==rates.INDEX:return b'<a href="https://www.pge.com/synthetic-old.xlsx">Industrial_B-20 (JAN 1, 2026 - JAN 31, 2026)</a><a href="https://www.pge.com/synthetic-new.xlsx">Industrial_B-20 (FEB 1, 2026 - Present)</a>'
        if url.endswith("old.xlsx"):return workbook_for("2026-01-01")
        if fail_second:raise ValueError("Synthetic unavailable workbook")
        return workbook_for("2026-02-01")
    monkeypatch.setattr(rates,"download",download);return calls


def test_historical_import_gets_all_versions_once_and_uses_private_cache(store,monkeypatch):
    cfg=configure(store,[("2026-01","B-20"),("2026-02","B-20"),("2026-03","B-20")])
    calls=synthetic_download(monkeypatch)
    result=asyncio.run(rates.fetch_tariff_history(store,cfg,"2026-01","2026-04"))
    assert result["complete"] and len(result["tariff_ids"])==2
    assert len(calls)==3 and len(list((store.directory/"tariff-sources").glob("*.xlsx")))==2
    assert all(c["complete"] for c in result["coverage"])
    asyncio.run(rates.fetch_tariff_history(store,cfg,"2026-01","2026-04"))
    assert calls.count("https://www.pge.com/synthetic-old.xlsx")==1


def test_import_failure_retains_rates_and_closes_old_open_ended_version(store,monkeypatch):
    cfg=configure(store,[("2026-01","B-20"),("2026-02","B-20")]);synthetic_download(monkeypatch,fail_second=True)
    old=rates.retain_tariff(store,workbook_for("2026-01-01"),selection("B-20"),{"url":"https://www.pge.com/synthetic-old.xlsx","label":"Synthetic former Present version","effective_from":"2026-01-01","effective_to":None})
    result=asyncio.run(rates.fetch_tariff_history(store,cfg,"2026-01","2026-03"))
    assert not result["complete"] and len(result["errors"])==1
    assert result["coverage"][0]["complete"] and not result["coverage"][1]["complete"]
    assert len(store.tariffs())==1 and store.tariffs()[0]["id"]==old["id"]
    assert store.tariffs()[0]["effective_to"]=="2026-02-01"


def test_monthly_web_workflow_csv_and_history_job(tmp_path,monkeypatch):
    synthetic_download(monkeypatch)
    app=create_app(tmp_path,background=False,auth_required=False)
    with TestClient(app) as c:
        c.headers["X-CSRF-Token"]=c.get("/api/session").json()["csrf"]
        cfg=configure(app.state.store,[("2026-01","B-20"),("2026-02","B-20")])
        cfg.meters[0].name="=SYNTHETIC";app.state.store.set("configuration",cfg.model_dump())
        fill(app.state.store,date(2026,1,1),date(2026,3,1),1)
        body={"start_month":"2026-01","end_month":"2026-03","revision":"stale"}
        assert c.post("/api/rates/history",json=body).status_code==409
        body["revision"]=app.state.store.revision()
        assert c.post("/api/rates/history",json=body).status_code==200
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            job=c.get("/api/rates/history").json()
            if job["status"]!="running":break
            time.sleep(.01)
        assert job["status"]=="completed",job
        report=c.get("/api/monthly-report",params={"start_month":"2026-01","end_month":"2026-03"})
        assert report.status_code==200 and report.json()["complete"]
        summary=c.get("/api/monthly-report.csv?start_month=2026-01&end_month=2026-03")
        assert len(list(csv.reader(io.StringIO(summary.text))))==3
        detail=c.get("/api/monthly-report.csv?start_month=2026-01&end_month=2026-03&detail=meters")
        assert "'=SYNTHETIC" in detail.text
        assert c.get("/api/monthly-report?start_month=bad&end_month=2026-03").status_code==400
        assert c.get("/api/monthly-report.csv?start_month=2026-01&end_month=2026-03&detail=unknown").status_code==400


def test_interrupted_import_is_visible_after_restart(tmp_path):
    s=Store(tmp_path);s.set("historical_rate_import",{"status":"running"});s.close()
    with TestClient(create_app(tmp_path,background=False,auth_required=False)) as c:
        assert c.get("/api/rates/history").json()["status"]=="interrupted"
