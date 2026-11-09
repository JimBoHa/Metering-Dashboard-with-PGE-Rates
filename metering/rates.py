"""Fetch PG&E-published workbooks at runtime. No tariff prices ship with the code."""
from __future__ import annotations
import hashlib
import asyncio
import io
import json
import os
import tempfile
import re
import time
from datetime import datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, parse_qs
from zoneinfo import ZoneInfo
from .periods import months_between
import httpx
import openpyxl

INDEX = "https://www.pge.com/tariffs/en/rate-information/electric-rates.html"
TARIFF_INDEX = "https://www.pge.com/tariffs/en.html"
SUPPORTED = ("B-6", "B-19", "B-20")


def normalize_schedule(value):
    value=re.sub(r"[^A-Z0-9]", "", value.upper())
    match=re.fullmatch(r"B(6|19|20)",value)
    if not match: raise ValueError("Automatic workbook pricing supports B-6, B-19 and B-20. Choose the exact schedule from your bill; other schedules need a verified importer.")
    return "B-"+match[1]


def period_at(timestamp, schedule, zone="America/Los_Angeles"):
    dt=datetime.fromtimestamp(timestamp, ZoneInfo(zone))
    season="summer" if 6<=dt.month<=9 else "winter"
    hour=dt.hour+dt.minute/60+dt.second/3600
    if 16<=hour<21: period="peak"
    elif season=="summer" and schedule in ("B-19","B-20") and (14<=hour<16 or 21<=hour<23): period="partial_peak"
    elif season=="winter" and 3<=dt.month<=5 and 9<=hour<14: period="super_off_peak"
    else: period="off_peak"
    return season,period


class Links(HTMLParser):
    def __init__(self): super().__init__();self.href=None;self.label=[];self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=="a": self.href=dict(attrs).get("href");self.label=[]
    def handle_data(self,data):
        if self.href: self.label.append(data)
    def handle_endtag(self,tag):
        if tag=="a" and self.href:
            self.links.append((self.href," ".join(self.label)));self.href=None


def safe_url(url):
    p=urlparse(url)
    if p.scheme!="https" or p.hostname not in {"www.pge.com","pge.com"} or p.username or p.password or p.port not in {None,443}:
        raise ValueError("Tariff downloads must remain on PG&E's official HTTPS website")
    return url


async def download(url):
    async with httpx.AsyncClient(timeout=45,follow_redirects=False,headers={"User-Agent":"MeteringDashboard/0.1 tariff-reader"}) as client:
        for _ in range(4):
            safe_url(url)
            async with client.stream("GET",url) as r:
                if r.status_code in {301,302,303,307,308}:
                    url=urljoin(url,r.headers["location"]);continue
                r.raise_for_status()
                data=bytearray()
                async for chunk in r.aiter_bytes():
                    data.extend(chunk)
                    if len(data)>15_000_000: raise ValueError("Tariff download exceeds size limit")
                if not data: raise ValueError("PG&E returned an empty file; no existing rate was replaced")
                return bytes(data)
        raise ValueError("Too many PG&E redirects")


def catalog(html):
    parser=Links();parser.feed(html)
    entries=[]
    for href,label in parser.links:
        group="commercial" if re.match(r"Commercial\s*\(B\)\s*\(",label,re.I) else "industrial" if re.match(r"Industrial[_\s-]*B-?20\s*\(",label,re.I) else None
        if not group: continue
        match=re.search(r"\(([A-Z]{3}\s+\d{1,2},\s*\d{4})\s*[-–]\s*(Present|[A-Z]{3}\s+\d{1,2},\s*\d{4})\)\s*$",label,re.I)
        if not match: continue
        date_from=datetime.strptime(match[1].upper(),"%b %d, %Y").date()
        date_to=None if match[2].lower()=="present" else (datetime.strptime(match[2].upper(),"%b %d, %Y").date()+timedelta(days=1)).isoformat()
        parsed=urlparse(href)
        direct=parse_qs(parsed.query).get("src",[urljoin(INDEX,href)])[0]
        safe_url(direct)
        if not urlparse(direct).path.lower().endswith(".xlsx"): continue
        entries.append({"group":group,"label":label,"url":direct,"effective_from":date_from.isoformat(),"effective_to":date_to})
    if not entries: raise ValueError("PG&E's workbook index format changed; retained tariffs remain available")
    return entries


def text(value): return re.sub(r"\s+"," ",str(value or "")).strip()
def number(value):
    if isinstance(value,(float,int)) and not isinstance(value,bool): return float(value)
    return None

def energy_period(label):
    label=re.sub(r"[^a-z]","",label.lower())
    if label=="superoffpeak": return "super_off_peak"
    if label=="offpeak": return "off_peak"
    if label in {"partpeak","partialpeak"}: return "partial_peak"
    if label in {"peak","onpeak"}: return "peak"
    return None

def demand_period(label):
    label=re.sub(r"[^a-z0-9]","",label.lower()).replace("maxium","maximum")
    if "maximum1" in label: return "maximum_excluding_solar"
    if "maximum2" in label or label in {"maximum","max"}: return "maximum"
    if "part" in label and "peak" in label: return "partial_peak"
    if "peak" in label: return "peak"
    return None


def parse_workbook(content, selection, entry):
    schedule=normalize_schedule(selection.schedule)
    if selection.service!="bundled": raise ValueError("PG&E's published summary workbooks contain bundled prices. Delivery-only/CCA pricing needs verified component tariffs; bundled prices will not be substituted.")
    if schedule=="B-6" and selection.option!="base": raise ValueError("B-6 has no R/S option in the published workbook")
    wb=openpyxl.load_workbook(io.BytesIO(content),read_only=True,data_only=True)
    sheets=[]
    for name in wb.sheetnames:
        option=re.search(r"(?:^|[^A-Za-z])OPT(?:ION)?\s*([RS])(?:$|[^A-Za-z])",name,re.I)
        if "time" in name.lower():continue
        if (selection.option=="base" and not option) or (option and option[1].upper()==selection.option):sheets.append(name)
    if len(sheets)!=1: raise ValueError("PG&E workbook sheet selection is ambiguous; no rates activated")
    sheet=wb[sheets[0]]
    sheet_date=re.search(r"[_ ](\d{6})[-_]",sheet.title)
    if sheet_date and datetime.strptime(sheet_date[1],"%y%m%d").date().isoformat()!=entry["effective_from"]:
        raise ValueError("PG&E index and workbook effective dates disagree; no rates activated")
    rows=list(sheet.iter_rows())
    energy={"summer":{},"winter":{}};demand=[];provenance=[];customer=None
    voltage_index={"secondary":0,"primary":1,"transmission":2}[selection.voltage]
    if schedule=="B-6" and selection.voltage!="secondary": raise ValueError("B-6 summary is a single-voltage schedule; select secondary")
    if schedule in {"B-6","B-19"}:
        # Locate both the schedule block and the energy-label column by labels.
        starts=[]
        for i,row in enumerate(rows):
            a=text(row[0].value)
            if re.match(r"^B-?\s*(?:1|6|10|19)\b",a,re.I): starts.append((i,a))
        target=next((i for i,a in starts if re.match(r"^B-?\s*"+schedule[2:]+r"\b",a,re.I)),None)
        if target is None: raise ValueError("Selected schedule is absent from the PG&E workbook")
        stop=next((i for i,a in starts if i>target),len(rows))
        season=None
        for row in rows[target:stop]:
            for cell in row:
                v=text(cell.value).lower()
                if v in {"summer","winter"}:season=v
            for j,cell in enumerate(row):
                label=text(cell.value)
                ep=energy_period(label)
                if ep and season and j+1+voltage_index<len(row):
                    # B-6's three voltage columns are merged; read the first value.
                    ix=j+1+(0 if schedule=="B-6" else voltage_index)
                    rate=number(row[ix].value)
                    if rate is not None:
                        energy[season][ep]=rate;provenance.append({"kind":"energy","season":season,"period":ep,"cell":row[ix].coordinate})
                if schedule=="B-19":
                    dp=demand_period(label)
                    if dp and not ep and season and j+1+voltage_index<len(row):
                        rate=number(row[j+1+voltage_index].value)
                        if rate is not None:
                            demand.append({"season":season,"period":dp,"price":rate,"basis":"daily" if selection.option=="S" and dp in {"peak","partial_peak"} else "monthly"})
                            provenance.append({"kind":"demand","cell":row[j+1+voltage_index].coordinate})
                if schedule=="B-6" and label.lower()==("single-phase" if selection.phase=="single" else "poly-phase"):
                    customer=number(row[j+1].value);provenance.append({"kind":"customer","cell":row[j+1].coordinate})
                if schedule=="B-19":
                    wanted=("Voluntary" in label) if selection.customer_class=="voluntary" else ("Mandatory" in label and re.search(r"\b"+selection.voltage[0].upper()+r"\s*:",label))
                    if wanted:
                        customer=number(row[j+1].value);provenance.append({"kind":"customer","cell":row[j+1].coordinate})
    else:
        # Industrial workbook repeats a separate, labelled block per voltage.
        starts=[i for i,row in enumerate(rows) if re.match(r"^B-?20\b",text(row[0].value),re.I)]
        target=next((i for i in starts if selection.voltage in text(rows[i][0].value).lower()),None)
        if target is None: raise ValueError("Selected voltage is absent from the PG&E workbook")
        stop=next((i for i in starts if i>target),len(rows));season=None
        customer=number(rows[target][1].value);provenance.append({"kind":"customer","cell":rows[target][1].coordinate})
        for row in rows[target:stop]:
            if text(row[2].value).lower() in {"summer","winter"}: season=text(row[2].value).lower()
            if not season:continue
            ep=energy_period(text(row[5].value));er=number(row[6].value)
            if ep and er is not None:
                energy[season][ep]=er;provenance.append({"kind":"energy","season":season,"period":ep,"cell":row[6].coordinate})
            dp=demand_period(text(row[3].value));dr=number(row[4].value)
            if dp and dr is not None:
                demand.append({"season":season,"period":dp,"price":dr,"basis":"daily" if selection.option=="S" and dp in {"peak","partial_peak"} else "monthly"})
                provenance.append({"kind":"demand","cell":row[4].coordinate})
    wb.close()
    required={"summer":{"peak","off_peak"},"winter":{"peak","off_peak","super_off_peak"}}
    if schedule!="B-6":required["summer"].add("partial_peak")
    if any(not required[s].issubset(energy[s]) for s in required) or customer is None:
        raise ValueError("PG&E workbook is incomplete or its format changed; no partial tariff activated")
    if schedule!="B-6":
        required_d={"peak","maximum","partial_peak"}
        if selection.option=="S": required_d.add("maximum_excluding_solar")
        if not required_d.issubset({d["period"] for d in demand}):raise ValueError("PG&E demand components could not be verified")
    if any(v<0 or v>100 for season in energy.values() for v in season.values()) or any(d["price"]<0 for d in demand): raise ValueError("Unexpected tariff value; inspect source before activation")
    return {"selection":{**selection.model_dump(),"schedule":schedule},"effective_from":entry["effective_from"],"effective_to":entry["effective_to"],"energy":energy,"demand":demand,"customer_daily":customer,"source_url":entry["url"],"index_url":INDEX,"source_label":entry["label"],"sheet":sheet.title,"cells":provenance,"source_sha256":hashlib.sha256(content).hexdigest(),"fetched_at":time.time(),"time_definition_url":f"https://www.pge.com/tariffs/assets/pdf/tariffbook/ELEC_SCHEDS_{schedule}.pdf"}


def retain_tariff(store,content,selection,entry,data=None):
    data=data if data is not None else parse_workbook(content,selection,entry)
    data["id"]=hashlib.sha256((data["source_sha256"]+json.dumps(data["selection"],sort_keys=True)+data["effective_from"]).encode()).hexdigest()[:24]
    directory=store.directory/"tariff-sources";directory.mkdir(exist_ok=True,mode=0o700)
    path=directory/(data["source_sha256"]+".xlsx")
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=data["source_sha256"]:
        with tempfile.NamedTemporaryFile(dir=directory,delete=False) as temporary:
            temporary.write(content);temporary.flush();os.fsync(temporary.fileno())
        os.replace(temporary.name,path)
    os.chmod(path,0o600)
    store.save_tariff(data)
    store.audit("tariff.import",{"tariff_id":data["id"],"source_url":entry["url"]})
    return data


def reconcile_catalog(store,entries):
    # "Present" can acquire an end date when PG&E publishes a new version.
    for tariff in store.tariffs():
        entry=next((e for e in entries if e["url"]==tariff.get("source_url") and e["effective_from"]==tariff["effective_from"]),None)
        if entry and tariff.get("effective_to")!=entry["effective_to"]:
            store.save_tariff({**tariff,"effective_to":entry["effective_to"]})


async def fetch_tariff(store, selection):
    schedule=normalize_schedule(selection.schedule)
    selection=selection.model_copy(update={"schedule":schedule})
    entries=catalog((await download(INDEX)).decode("utf-8"))
    reconcile_catalog(store,entries)
    group="industrial" if schedule=="B-20" else "commercial"
    today=datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
    options=[e for e in entries if e["group"]==group and e["effective_from"]<=today and (not e["effective_to"] or e["effective_to"]>today)]
    if not options:raise ValueError("No currently effective official workbook found")
    entry=max(options,key=lambda e:e["effective_from"])
    content=await download(entry["url"])
    parsed=await asyncio.to_thread(parse_workbook,content,selection,entry)
    data=retain_tariff(store,content,selection,entry,parsed)
    store.set("rate_sync",{"last_success":time.time(),"last_attempt":time.time(),"error":None,"tariff_id":data["id"],"selection":selection.model_dump()})
    return data


async def fetch_tariff_history(store,config,start_month,end_month,progress=None):
    """Import only official effective versions overlapping explicitly assigned months."""
    from .billing import rate_coverage
    periods=months_between(start_month,end_month)
    assigned={r.month:r.selection for r in config.monthly_rates}
    if not any(a.strftime("%Y-%m") in assigned for a,b in periods):
        raise ValueError("Save at least one monthly rate schedule before fetching historical rates")
    entries=catalog((await download(INDEX)).decode("utf-8"));reconcile_catalog(store,entries)
    needed={}
    for a,b in periods:
        selection=assigned.get(a.strftime("%Y-%m"))
        if not selection:continue
        group="industrial" if selection.schedule=="B-20" else "commercial"
        for entry in entries:
            if entry["group"]!=group or entry["effective_from"]>=b.isoformat() or (entry["effective_to"] and entry["effective_to"]<=a.isoformat()):continue
            key=(entry["url"],entry["effective_from"],entry["effective_to"])
            item=needed.setdefault(key,{"entry":entry,"selections":{}})
            item["selections"][json.dumps(selection.model_dump(),sort_keys=True)]=selection
    total=sum(len(item["selections"]) for item in needed.values())
    if total>180:raise ValueError("This request needs too many tariff combinations; import a shorter range")
    processed=0;imported=[];errors=[]
    def update(active=""):
        if progress:progress({"processed":processed,"total":total,"imported":len(imported),"errors":list(errors),"active":active})
    update()
    for item in sorted(needed.values(),key=lambda x:(x["entry"]["effective_from"],x["entry"]["group"])):
        entry=item["entry"];content=None
        update(entry["label"])
        try:
            if entry["effective_to"]:
                for saved in store.tariffs():
                    if saved.get("source_url")!=entry["url"] or saved["effective_from"]!=entry["effective_from"]:continue
                    digest=saved.get("source_sha256","")
                    if not re.fullmatch(r"[a-f0-9]{64}",digest):continue
                    path=store.directory/"tariff-sources"/(digest+".xlsx")
                    if path.is_file():
                        cached=path.read_bytes()
                        if hashlib.sha256(cached).hexdigest()==digest:content=cached;break
            if content is None:content=await download(entry["url"])
        except Exception as e:
            errors.append({"source":entry["label"],"error":str(e)[:500]});processed+=len(item["selections"]);update();continue
        for selection in item["selections"].values():
            try:
                parsed=await asyncio.to_thread(parse_workbook,content,selection,entry)
                data=retain_tariff(store,content,selection,entry,parsed);imported.append(data["id"])
            except Exception as e:errors.append({"source":entry["label"],"schedule":selection.schedule,"option":selection.option,"error":str(e)[:500]})
            processed+=1;update(entry["label"])
    tariffs=store.tariffs();coverage=[]
    for a,b in periods:
        month=a.strftime("%Y-%m");selection=assigned.get(month)
        coverage.append({"month":month,"assigned":selection is not None,**rate_coverage(selection.model_dump() if selection else None,a,b,tariffs)})
    return {"tariff_ids":sorted(set(imported)),"errors":errors,"coverage":coverage,"complete":not errors and all(c["complete"] for c in coverage)}
