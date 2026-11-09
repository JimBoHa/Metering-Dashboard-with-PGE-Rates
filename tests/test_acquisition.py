import asyncio
import json
import sqlite3
from metering.acquisition import read_platform, Acquisition
from metering.models import Source
from metering.store import Store
import pytest

def test_sqlite_adapter_does_not_write(tmp_path):
    p=tmp_path/'existing.sqlite3';db=sqlite3.connect(p)
    db.execute('CREATE TABLE current_samples(point_id TEXT,timestamp REAL,value TEXT,quality TEXT,units TEXT,error TEXT)')
    db.execute('INSERT INTO current_samples VALUES(?,?,?,?,?,?)',('counter',1000,'100','good','kilowatt-hours',None))
    db.execute('INSERT INTO current_samples VALUES(?,?,?,?,?,?)',('switch',1000,'true','good','no-units',None));db.commit();db.close()
    before=p.read_bytes()
    source=Source(id='existing',name='Synthetic source',kind='platform_sqlite',settings={'path':str(p)})
    assert read_platform(source)[0]['value']==100
    assert len(read_platform(source))==1
    assert read_platform(source,{'point_id':'counter'})['value']=='100'
    assert p.read_bytes()==before

def test_bacnet_protocol_abort_becomes_normal_error(tmp_path):
    from bacpypes3.apdu import AbortPDU
    class Device:
        async def read_property(self,*args,**kwargs):raise AbortPDU(reason='segmentation-not-supported')
    s=Store(tmp_path/'own');a=Acquisition(s)
    with pytest.raises(ValueError,match='segmentation-not-supported'):
        asyncio.run(a.read_property(Device(),'unused',('device',1),'object-list'))
    s.close()
