"""Bounded read-only adapters. No BACnet writes, serial access or Modbus writes."""
from __future__ import annotations
import asyncio
import hashlib
import json
import math
import sqlite3
import struct
import time
from contextlib import closing
from pathlib import Path
from .models import Reading


def numeric(value):
    if isinstance(value,bool):raise ValueError("Boolean points are not usage meters")
    v=float(value)
    if not math.isfinite(v):raise ValueError("Non-finite reading")
    return v


def read_platform(source, point=None):
    path=Path(source.settings["path"]).expanduser().resolve()
    if not path.is_file():raise ValueError("Configured source database does not exist")
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=2)) as db:
        db.execute("PRAGMA query_only=ON")
        db.row_factory=sqlite3.Row
        if point:
            row=db.execute("SELECT * FROM current_samples WHERE point_id=?",(point["point_id"],)).fetchone()
            if not row:raise ValueError("Point not present in source database")
            return dict(row)
        rows=db.execute("SELECT point_id,value,units,quality,timestamp FROM current_samples LIMIT 2000").fetchall()
        result=[]
        for row in rows:
            try:value=numeric(json.loads(row["value"]))
            except (ValueError,TypeError):continue
            result.append({"name":row["point_id"],"point":{"point_id":row["point_id"]},"value":value,"units":row["units"],"quality":row["quality"],"timestamp":row["timestamp"]})
        return result


class Acquisition:
    def __init__(self,store):
        self.store=store;self.apps={};self.lock=asyncio.Lock();self.status={};self.last_poll=None;self.identity_cache={}

    async def bacnet(self,source):
        from bacpypes3.app import Application
        from bacpypes3.argparse import SimpleArgumentParser
        bind=source.settings.get("bind","")
        port=bind.rsplit(":",1)[-1]
        if not bind or not port.isdigit() or int(port)<1024 or int(port)==47808:
            raise ValueError("BACnet reader bind must use an explicit unused UDP port other than 47808 (for example :47810)")
        identity=json.dumps(source.settings,sort_keys=True)
        if source.id in self.apps and self.apps[source.id][0]!=identity:
            self.apps.pop(source.id)[1].close()
        if source.id not in self.apps:
            instance=int(source.settings.get("instance",4194290))
            if not 0<=instance<=4194302:raise ValueError("Invalid reader device instance")
            args=SimpleArgumentParser().parse_args(["--address",bind,"--instance",str(instance),"--name","Metering-Reader"])
            self.apps[source.id]=(identity,Application.from_args(args))
            await asyncio.sleep(.15)
        return self.apps[source.id][1]

    async def read_property(self,app,address,obj,prop,index=None):
        from bacpypes3.primitivedata import ObjectIdentifier
        if isinstance(obj,tuple):obj=ObjectIdentifier(obj)
        from bacpypes3.apdu import ErrorRejectAbortNack
        try:
            return await asyncio.wait_for(app.read_property(address,obj,prop,array_index=index),timeout=3)
        except ErrorRejectAbortNack as e:
            raise ValueError(str(e)) from e
        finally:await asyncio.sleep(.06)

    async def discover(self,source,device=None,offset=1,limit=40):
        if not source.enabled:raise ValueError("Source is disabled")
        async with self.lock:
            if source.kind=="platform_sqlite":return {"points":await asyncio.to_thread(read_platform,source)}
            if source.kind=="http_json":
                data=await self.http(source)
                def flatten(obj,prefix="",depth=0):
                    if depth>8:return []
                    if isinstance(obj,dict):return [entry for k,v in obj.items() for entry in flatten(v,f"{prefix}.{k}".strip("."),depth+1)]
                    if isinstance(obj,list):return [entry for k,v in enumerate(obj[:500]) for entry in flatten(v,f"{prefix}.{k}".strip("."),depth+1)]
                    try:return [{"name":prefix,"point":{"path":prefix},"value":numeric(obj)}]
                    except (ValueError,TypeError):return []
                return {"points":flatten(data)[:1000]}
            if source.kind=="modbus":
                values=await self.modbus(source,{"register":int(source.settings.get("probe_register",0)),"count":min(32,int(source.settings.get("probe_count",4))),"encoding":"registers"})
                return {"registers":values,"note":"Only the configured register block was read. Use the manufacturer's register map, units and scaling."}
            app=await self.bacnet(source)
            if not device:
                from bacpypes3.pdu import Address
                target=source.settings.get("target")
                if not target:raise ValueError("Enter a directed target or your subnet broadcast address and destination port")
                found=list(await app.who_is(address=Address(target),timeout=3))
                cidr=source.settings.get("scan_cidr")
                if cidr:
                    import ipaddress
                    net=ipaddress.ip_network(cidr,strict=False)
                    if net.version!=4 or not net.is_private or net.num_addresses>256:
                        raise ValueError("Directed discovery requires a private IPv4 subnet of /24 or smaller")
                    pending=[]
                    for host in net.hosts():
                        pending.append(app.who_is(address=Address(str(host)+":47808"),timeout=1))
                        await asyncio.sleep(.2)
                    for responses in await asyncio.gather(*pending):found.extend(responses)
                found=list({str(r.pduSource):r for r in found}.values())
                result=[]
                for r in found[:100]:
                    address=str(r.pduSource);obj=r.iAmDeviceIdentifier
                    try:name=str(await self.read_property(app,address,obj,"object-name"))
                    except Exception:name="Device "+str(obj[1])
                    result.append({"address":address,"instance":int(obj[1]),"name":name,"vendor_id":int(r.vendorID)})
                return {"devices":result,"note":"One bounded Who-Is; responses on the selected BACnet network only."}
            address=device["address"];obj=("device",int(device["instance"]))
            count=int(await self.read_property(app,address,obj,"object-list",0))
            points=await self.object_page(app,address,obj,offset,min(count+1,offset+limit))
            return {"points":points,"object_count":count,"next_offset":offset+limit if offset+limit<=count else None}

    async def rpm(self,app,address,params):
        from bacpypes3.apdu import ErrorRejectAbortNack
        try:result=await asyncio.wait_for(app.read_property_multiple(address,params),timeout=5)
        except ErrorRejectAbortNack as e:raise ValueError(str(e)) from e
        if not isinstance(result,list):raise ValueError("Device does not support this read-multiple request")
        await asyncio.sleep(.15)
        return result

    async def object_page(self,app,address,obj,start,stop):
        from bacpypes3.primitivedata import ObjectIdentifier
        from bacpypes3.basetypes import PropertyReference
        obj=ObjectIdentifier(obj) if isinstance(obj,tuple) else obj
        objects=[]
        for i in range(start,stop,16):
            indices=list(range(i,min(i+16,stop)))
            try:
                values=await self.rpm(app,address,[obj,[PropertyReference(propertyIdentifier="object-list",propertyArrayIndex=j) for j in indices]])
                objects.extend(v for _,_,_,v in values if isinstance(v,ObjectIdentifier))
            except Exception:
                for j in indices:
                    try:objects.append(await self.read_property(app,address,obj,"object-list",j))
                    except Exception:pass
        objects=[o for o in objects if str(o[0]) in {"analog-input","analog-output","analog-value","accumulator","pulse-converter","large-analog-value","integer-value","positive-integer-value"}]
        points=[]
        for i in range(0,len(objects),8):
            block=objects[i:i+8];params=[];metadata={}
            for o in block:params.extend([o,["object-name","units","description"]])
            try:
                values=await self.rpm(app,address,params)
                for o,p,_,v in values:
                    if isinstance(v,(str,int,float)):metadata.setdefault(str(o),{})[str(p)]=str(v)
            except Exception:
                for o in block:
                    try:
                        values=await self.rpm(app,address,[o,["object-name","units","description"]])
                        for _,p,_,v in values:
                            if isinstance(v,(str,int,float)):metadata.setdefault(str(o),{})[str(p)]=str(v)
                    except Exception:
                        for p in ["object-name","units","description"]:
                            try:metadata.setdefault(str(o),{})[p]=str(await self.read_property(app,address,o,p))
                            except Exception:pass
            for o in block:
                data=metadata.get(str(o),{})
                if not data.get("object-name"):continue
                points.append({"name":data["object-name"],"units":data.get("units","unknown"),"description":data.get("description",""),"point":{"address":address,"object_type":str(o[0]),"object_instance":int(o[1]),"expected_name":data["object-name"],"expected_units":data.get("units"),"unverified_reason":"Source reports display comparison pending" if "display comparison pending" in data.get("description","").lower() else None}})
        return points

    async def http(self,source):
        import httpx
        from urllib.parse import urlparse
        url=source.settings.get("url","")
        p=urlparse(url)
        if p.scheme not in {"http","https"} or not p.hostname:raise ValueError("HTTP source requires an http(s) URL")
        async with httpx.AsyncClient(timeout=5,follow_redirects=False) as client:
            async with client.stream("GET",url,headers=source.settings.get("headers",{})) as r:
                r.raise_for_status();data=bytearray()
                async for chunk in r.aiter_bytes():
                    data.extend(chunk)
                    if len(data)>2_000_000:raise ValueError("Source response exceeds size limit")
                return json.loads(data)

    async def modbus(self,source,point):
        from pymodbus.client import AsyncModbusTcpClient
        client=AsyncModbusTcpClient(source.settings["host"],port=int(source.settings.get("port",502)),timeout=3,retries=0)
        try:
            if not await client.connect():raise ValueError("Modbus TCP connection failed")
            encoding=point.get("encoding","float32")
            count=point.get("count",2 if encoding in {"float32","uint32","int32"} else 4 if encoding in {"float64","uint64"} else 1)
            address=int(point["register"])
            if not 0<=address<=65535 or not 1<=count<=32 or address+count>65536:raise ValueError("Invalid read register block")
            method=client.read_input_registers if point.get("table")=="input" else client.read_holding_registers
            response=await method(address,count=count,slave=int(source.settings.get("unit_id",1)))
            if response.isError():raise ValueError(str(response))
            registers=response.registers
            if encoding=="registers":return registers
            if point.get("word_order","big")=="little":registers=list(reversed(registers))
            raw=b"".join(int(v).to_bytes(2,point.get("byte_order","big")) for v in registers)
            formats={"float32":">f","float64":">d","uint16":">H","int16":">h","uint32":">I","int32":">i","uint64":">Q"}
            if encoding not in formats:raise ValueError("Unsupported Modbus encoding")
            return numeric(struct.unpack(formats[encoding],raw)[0])
        finally:client.close()

    async def read_meter(self,meter,source,http_cache):
        now=time.time();quality="good";error=None
        if source.kind=="platform_sqlite":
            r=await asyncio.to_thread(read_platform,source,meter.point)
            value=numeric(json.loads(r["value"]));now=r["timestamp"];quality=r["quality"];error=r.get("error")
        elif source.kind=="http_json":
            if source.id not in http_cache:http_cache[source.id]=await self.http(source)
            data=http_cache[source.id]
            def get(path):
                obj=data
                for key in path.split("."):obj=obj[int(key)] if isinstance(obj,list) else obj[key]
                return obj
            value=numeric(get(meter.point["path"]))
            if meter.point.get("timestamp_path"):now=numeric(get(meter.point["timestamp_path"]))
        elif source.kind=="modbus":value=await self.modbus(source,meter.point)
        else:
            app=await self.bacnet(source);p=meter.point
            obj=(p["object_type"],int(p["object_instance"]))
            expected=p.get("expected_name")
            identity=(source.id,p["address"],str(obj),expected,p.get("expected_units"))
            if not expected:
                raise ValueError("Verify this BACnet point's identity in meter configuration before collection")
            if time.time()-self.identity_cache.get(identity,0)>300:
                name=str(await self.read_property(app,p["address"],obj,"object-name"))
                units=str(await self.read_property(app,p["address"],obj,"units"))
                if name!=expected or (p.get("expected_units") and units!=p["expected_units"]):
                    raise ValueError("BACnet point identity or units changed; rediscover and review this meter")
                self.identity_cache[identity]=time.time()
            value=numeric(await self.read_property(app,p["address"],obj,"present-value"))
            try:
                flags=await self.read_property(app,p["address"],obj,"status-flags")
                if flags[1] or flags[3]:quality="fault";error="BACnet fault or out-of-service flag"
            except Exception:quality="unverified";error="BACnet status flags unavailable"
            if p.get("unverified_reason") and not p.get("commissioned") and quality=="good":
                quality="unverified";error=p["unverified_reason"]
        if now>time.time()+60:raise ValueError("Source timestamp is in the future")
        stream={"source_kind":source.kind,"source_settings":source.settings,"point":meter.point,"mode":meter.mode,"unit":meter.unit,"scale":meter.scale,"offset":meter.offset,"rate_period_seconds":meter.rate_period_seconds,"interval_seconds":meter.interval_seconds}
        stream_id=hashlib.sha256(json.dumps(stream,sort_keys=True).encode()).hexdigest()
        return Reading(meter_id=meter.id,timestamp=now,value=value,quality=quality,error=error,stream_id=stream_id)

    async def poll(self):
        async with self.lock:
            cfg=self.store.config();sources={s.id:s for s in cfg.sources};cache={}
            for m in cfg.meters:
                if not m.enabled or not m.source_id:continue
                source=sources[m.source_id]
                if not source.enabled:continue
                try:
                    reading=await self.read_meter(m,source,cache)
                    self.store.add_reading(reading)
                    self.status[m.id]={"last_success":time.time(),"quality":reading.quality,"error":reading.error}
                except Exception as e:self.status[m.id]={"last_error":time.time(),"quality":"offline","error":str(e)[:300]}
            self.last_poll=time.time()
    async def run(self):
        while True:
            try:await self.poll()
            except asyncio.CancelledError:raise
            except Exception as e:self.status["collector"]={"error":str(e)[:300]}
            await asyncio.sleep(self.store.config().poll_seconds)
    def close(self):
        for _,app in self.apps.values():app.close()
        self.apps.clear()
