"""Synthetic DB-API fixture; never connects to a real server or contains site data."""
from datetime import datetime, timedelta
import re

CATALOG={
 ('dbo','tblPoint'):[('PointID','int'),('PointName','nvarchar'),('PointDataTypeID','int'),('UnitOfMeasureID','int')],
 ('dbo','tblPointSlice'):[('PointID','int'),('PointSliceID','int'),('IsRawData','bit')],
 ('dbo','tblActualValueFloat'):[('PointSliceID','int'),('UTCDateTime','datetime'),('ActualValue','real')],
 ('dbo','tblUnitOfMeasure'):[('UnitOfMeasureID','int'),('UnitOfMeasureName','nvarchar')],
 ('reporting','Samples'):[('PointID','int'),('SampleTime','datetime2'),('Value','float'),('Quality','nvarchar')],
 ('reporting','Points'):[('PointID','int'),('Name','nvarchar'),('Path','nvarchar'),('Units','nvarchar')],
}
class SyntheticSql:
    def __init__(self,count=3):
        self.calls=[];self.connect_calls=[];self.count=count;self.failed=False;self.conflicting=False;self.quality=1
        self.points=[{'point_id':'101','point_name':'Demo/Building A/Electrical/Energy','point_path':'Demo/Building A/Electrical/Energy','units':'kilowatt-hours'},
                     {'point_id':'102','point_name':'Demo/Building A/Water/Volume','point_path':'Demo/Building A/Water/Volume','units':'us-gallons'}]
    def connect(self,**kwargs):
        self.connect_calls.append(kwargs)
        if self.failed:raise RuntimeError('synthetic-password-from-driver must never be exposed')
        return Connection(self)
    def query(self,sql,params):
        assert sql.startswith('SELECT ')
        assert sql.count('%s')==len(params),(sql,params)
        self.calls.append((sql,params))
        if 'sys.objects o' in sql:return [{'schema_name':k[0],'table_name':k[1],'column_name':c,'data_type':t} for k,cols in CATALOG.items() for c,t in cols]
        if 'DB_NAME()' in sql:return [{'name':'SyntheticHistory'}]
        if 'sys.databases' in sql:return [{'name':'SyntheticHistory'}]
        if 'AS point_name' in sql:
            rows=list(self.points);values=list(params[:-2])
            if 'AND p.[PointID]=%s' in sql:
                pid=str(values.pop(0));rows=[r for r in rows if r['point_id']==pid]
            if values:
                search=values[0].strip('%').replace('\\','').lower()
                rows=[r for r in rows if search in r['point_name'].lower() or search in r['point_path'].lower()]
            return rows[params[-2]:params[-2]+params[-1]]
        if 'AS sample_time' in sql:
            offset=sql.split(' FROM ')[0].count('%s');pid,start,end,*after=params[offset:]
            def dt(v):return datetime.fromisoformat(v) if isinstance(v,str) else v
            start,end=dt(start),dt(end);after=dt(after[0]) if after else None
            count=int(re.search(r'TOP \((\d+)\)',sql)[1]);rows=[]
            for i in range(self.count):
                stamp=datetime(2026,11,1,12)+timedelta(minutes=15*i)
                if start<=stamp<end and (after is None or stamp>after):
                    val=(100 if str(pid)=='101' else 1000)+i*10
                    rows.append({'sample_time':stamp,'sample_value':val,'maximum_value':val+1 if self.conflicting else val,'accepted_quality':self.quality,'null_count':0})
            return rows[:count]
        raise AssertionError('Unexpected SQL query: '+sql)
class Connection:
    def __init__(self,db):self.db=db
    def cursor(self):return Cursor(self.db)
    def close(self):pass
class Cursor:
    def __init__(self,db):self.db=db
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,params):self.rows=self.db.query(sql,params)
    def fetchall(self):return self.rows

def settings(**overrides):
    return {'host':'synthetic.invalid','database':'SyntheticHistory','username':'synthetic_reader','profile':'auto','missing_quality':'good',**overrides}

def mapping():
    return {'schema_name':'reporting','table':'Samples','point_id_column':'PointID','timestamp_column':'SampleTime','value_column':'Value','quality_column':'Quality',
            'point_schema':'reporting','point_table':'Points','point_key_column':'PointID','point_name_column':'Name','point_path_column':'Path','point_unit_column':'Units'}
