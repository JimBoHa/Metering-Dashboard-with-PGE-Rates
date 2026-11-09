"""SQL Server SELECT-only historian adapter. Identifiers come from an inspected catalog;
point IDs, filters, dates and paging values are bound parameters, never SQL text.
"""
from __future__ import annotations
import hashlib
import json
import math
import os
import tempfile
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import certifi
from .sql_models import SqlSettings, TrendMapping

NUMERIC_TYPES={"tinyint","smallint","int","bigint","decimal","numeric","float","real","money","smallmoney"}
DATE_TYPES={"date","datetime","datetime2","smalldatetime","datetimeoffset"}

class SqlTrendError(ValueError):pass

def quote(name):
    if not isinstance(name,str) or not 1<=len(name)<=128 or '\x00' in name:raise SqlTrendError("Invalid SQL identifier")
    return '['+name.replace(']',']]')+']'

def table_ref(schema,table):return quote(schema)+'.'+quote(table)

def like_filter(value):
    return '%'+re.sub(r'([\\%_\[])',r'\\\1',value)+'%'

def sql_timestamp(value,kind,zone):
    if kind!="datetime":
        result=float(value)/(1000 if kind=="unix_milliseconds" else 1)
    else:
        if not isinstance(value,datetime):raise SqlTrendError("The timestamp column did not return a SQL date/time")
        if value.tzinfo is None:
            tz=ZoneInfo(zone);candidates=set()
            for fold in (0,1):
                candidate=value.replace(tzinfo=tz,fold=fold)
                if candidate.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)==value:candidates.add(candidate.timestamp())
            if len(candidates)!=1:raise SqlTrendError("An ambiguous or nonexistent local timestamp was found; use UTC or datetimeoffset data")
            result=candidates.pop()
        else:result=value.timestamp()
    if not math.isfinite(result) or result<=0:raise SqlTrendError("The remote timestamp is invalid")
    return result

def sql_bound(stamp,kind,zone,offset=False):
    if kind!="datetime":return stamp*(1000 if kind=="unix_milliseconds" else 1)
    value=datetime.fromtimestamp(stamp,timezone.utc)
    return value if offset else value.astimezone(ZoneInfo(zone)).replace(tzinfo=None)

class SqlReader:
    def __init__(self,settings,password,directory,connector=None):
        self.settings=SqlSettings.model_validate(settings);self.password=password;self.directory=Path(directory)
        if connector is None:
            import pytds
            connector=pytds.connect
        self.connector=connector
    @contextmanager
    def connection(self):
        s=self.settings;ca=None
        if s.tls=="verify":
            ca=certifi.where()
            if s.ca_pem:
                folder=self.directory/'sql-trust';folder.mkdir(exist_ok=True,mode=0o700)
                path=folder/(hashlib.sha256(s.ca_pem.encode()).hexdigest()+'.pem')
                if not path.exists():
                    fd,temp=tempfile.mkstemp(dir=folder)
                    try:
                        with os.fdopen(fd,'w') as f:f.write(s.ca_pem)
                        os.replace(temp,path)
                    finally:
                        if os.path.exists(temp):os.unlink(temp)
                ca=str(path)
        try:
            conn=self.connector(server=s.host,port=s.port,database=s.database or None,user=s.username,password=self.password,
                timeout=15,login_timeout=8,readonly=True,autocommit=True,appname="MeteringDashboard",cafile=ca,
                validate_host=True,enc_login_only=False,disable_connect_retry=True,pooling=False,as_dict=True)
        except Exception:raise SqlTrendError("SQL connection failed. Check server, port, database, login permissions and the TLS certificate.") from None
        try:yield conn
        finally:conn.close()
    def query(self,conn,sql,params=()):
        if not sql.lstrip().upper().startswith('SELECT '):raise SqlTrendError("Only SELECT queries are supported")
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql,params)
                return list(cursor.fetchall())
        except Exception:raise SqlTrendError("SQL read failed or timed out. Check SELECT permissions, the selected schema, and the server's availability.") from None
    def catalog(self,conn):
        rows=self.query(conn,"SELECT TOP (20001) s.name AS schema_name,o.name AS table_name,c.name AS column_name,t.name AS data_type FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id JOIN sys.columns c ON c.object_id=o.object_id JOIN sys.types t ON t.user_type_id=c.user_type_id WHERE o.type IN ('U','V') AND o.is_ms_shipped=0 ORDER BY s.name,o.name,c.column_id")
        if len(rows)>20000:raise SqlTrendError("The database has too many columns to browse at once; use a login limited to the historian tables/views")
        tables={}
        for row in rows:
            key=(row['schema_name'],row['table_name'])
            tables.setdefault(key,[]).append({'name':row['column_name'],'type':row['data_type'].lower()})
        return tables
    def _columns(self,catalog,schema,table,columns):
        found=catalog.get((schema,table))
        if found is None:raise SqlTrendError("A configured table or view is absent from the accessible database catalog")
        types={c['name']:c['type'] for c in found}
        if any(c and c not in types for c in columns):raise SqlTrendError("A configured column is absent from the selected table/view")
        return types
    def resolve(self,catalog):
        required={('dbo','tblPoint'):{'PointID','PointName','PointDataTypeID'},('dbo','tblPointSlice'):{'PointID','PointSliceID','IsRawData'},('dbo','tblActualValueFloat'):{'PointSliceID','UTCDateTime','ActualValue'}}
        metasys=all(cols.issubset({c['name'] for c in catalog.get(key,[])}) for key,cols in required.items())
        profile=self.settings.profile
        if profile=="auto":profile="metasys" if metasys else "custom"
        if profile=="metasys" and not metasys:raise SqlTrendError("The standard Metasys analog historian views were not found; choose a custom mapping")
        if profile=="metasys":
            mapping={'profile':'metasys','tables':{str(k):catalog[k] for k in required},'units':catalog.get(('dbo','tblUnitOfMeasure'),[])}
            details={'profile':profile,'timestamp_kind':'datetime','zone':'UTC','offset':False,'value_type':'real'}
        else:
            m=self.settings.mapping
            if m is None:raise SqlTrendError("Choose the trend table and column mapping, then browse its points")
            types=self._columns(catalog,m.schema_name,m.table,[m.point_id_column,m.timestamp_column,m.value_column,m.quality_column])
            if types[m.value_column] not in NUMERIC_TYPES:raise SqlTrendError("The trend value column must use a numeric SQL type")
            if (m.timestamp_kind=="datetime" and types[m.timestamp_column] not in DATE_TYPES) or (m.timestamp_kind!="datetime" and types[m.timestamp_column] not in NUMERIC_TYPES):raise SqlTrendError("The timestamp format does not match the selected SQL column type")
            if m.point_table:self._columns(catalog,m.point_schema,m.point_table,[m.point_key_column,m.point_name_column,m.point_path_column,m.point_unit_column])
            mapping={'profile':profile,'mapping':m.model_dump(),'types':types}
            details={'profile':profile,'timestamp_kind':m.timestamp_kind,'zone':self.settings.timezone,'offset':types[m.timestamp_column]=='datetimeoffset','value_type':types[m.value_column]}
        details['signature']=hashlib.sha256(json.dumps(mapping,sort_keys=True).encode()).hexdigest()
        return details
    def suggest(self,catalog):
        # Suggestions are names only; they are never executed until the user saves them.
        for (schema,table),cols in catalog.items():
            byname={c['name'].lower():c for c in cols}
            def find(names,types=None):
                return next((byname[n]['name'] for n in names if n in byname and (not types or byname[n]['type'] in types)),None)
            pid=find(['pointid','point_id','meterid','meter_id','tagid','tag_id'])
            stamp=find(['utcdatetime','timestamp','sample_time','recorded_at','datetime','time'],DATE_TYPES|NUMERIC_TYPES)
            value=find(['actualvalue','value','sample_value','reading'],NUMERIC_TYPES)
            if pid and stamp and value:
                return TrendMapping(schema_name=schema,table=table,point_id_column=pid,timestamp_column=stamp,value_column=value).model_dump()
        return None
    def inspect(self):
        with self.connection() as conn:
            catalog=self.catalog(conn)
            database=self.query(conn,"SELECT DB_NAME() AS name")[0]['name']
            try:databases=[r['name'] for r in self.query(conn,"SELECT name FROM sys.databases WHERE state=0 AND HAS_DBACCESS(name)=1 ORDER BY name")]
            except SqlTrendError:databases=[database]
            try:resolved=self.resolve(catalog);profile=resolved['profile'];ready=True
            except SqlTrendError:profile='custom';ready=False
            return {'database':database,'databases':databases,'tables':[{'schema':k[0],'name':k[1],'columns':v} for k,v in catalog.items()],
                    'profile':profile,'ready':ready,'suggested_mapping':self.suggest(catalog),'message':'Select numeric analog trends and confirm units before billing.'}
    def point_select(self,catalog,resolved):
        if resolved['profile']=='metasys':
            pointcols={c['name'] for c in catalog[('dbo','tblPoint')]}
            unitcols={c['name'] for c in catalog.get(('dbo','tblUnitOfMeasure'),[])}
            units="N''";join=''
            if 'UnitOfMeasureID' in pointcols and {'UnitOfMeasureID','UnitOfMeasureName'}.issubset(unitcols):
                join=' LEFT JOIN [dbo].[tblUnitOfMeasure] u ON u.[UnitOfMeasureID]=p.[UnitOfMeasureID]'
                units='u.[UnitOfMeasureName]'
            return {'from':'[dbo].[tblPoint] p'+join,'id':'p.[PointID]','name':'p.[PointName]','path':'p.[PointName]','units':units,'where':'p.[PointDataTypeID]=1','distinct':False}
        m=self.settings.mapping
        if m.point_table:
            return {'from':table_ref(m.point_schema,m.point_table)+' p','id':'p.'+quote(m.point_key_column),'name':'p.'+quote(m.point_name_column),
                    'path':'p.'+quote(m.point_path_column or m.point_name_column),'units':'p.'+quote(m.point_unit_column) if m.point_unit_column else "N''",'where':'p.'+quote(m.point_key_column)+' IS NOT NULL','distinct':False}
        col='t.'+quote(m.point_id_column)
        return {'from':table_ref(m.schema_name,m.table)+' t','id':col,'name':col,'path':col,'units':"N''",'where':col+' IS NOT NULL','distinct':True}
    def _point_rows(self,conn,catalog,resolved,search='',offset=0,limit=500,point_id=None):
        p=self.point_select(catalog,resolved)
        fields=f"CAST({p['id']} AS nvarchar(512)) AS point_id,CAST({p['name']} AS nvarchar(2048)) AS point_name,CAST({p['path']} AS nvarchar(2048)) AS point_path,CAST({p['units']} AS nvarchar(128)) AS units"
        sql='SELECT '+('DISTINCT ' if p['distinct'] else '')+fields+' FROM '+p['from']+' WHERE '+p['where'];params=[]
        if point_id is not None:sql+=' AND '+p['id']+'=%s';params.append(point_id)
        if search:
            sql+=f" AND (CAST({p['name']} AS nvarchar(2048)) LIKE %s ESCAPE '\\' OR CAST({p['path']} AS nvarchar(2048)) LIKE %s ESCAPE '\\')"
            params.extend([like_filter(search)]*2)
        sql+=' ORDER BY point_id OFFSET %s ROWS FETCH NEXT %s ROWS ONLY';params.extend([offset,limit])
        return self.query(conn,sql,tuple(params))
    def point_info(self,row,resolved):
        pid=str(row['point_id']);name=str(row['point_name'] or pid);path=str(row['point_path'] or name);units=str(row['units'] or '')
        return {'id':pid,'name':name,'path':path,'units':units,'point':{'point_id':pid,'sql_profile':resolved['profile'],'mapping_signature':resolved['signature'],'expected_name':name,'expected_units':units}}
    def points(self,search='',offset=0,limit=500):
        with self.connection() as conn:
            catalog=self.catalog(conn);resolved=self.resolve(catalog)
            rows=self._point_rows(conn,catalog,resolved,search,offset,limit+1)
            return {'points':[self.point_info(r,resolved) for r in rows[:limit]],'next_offset':offset+limit if len(rows)>limit else None,'profile':resolved['profile']}
    def validate_point(self,conn,catalog,resolved,point):
        if point.get('mapping_signature')!=resolved['signature'] or point.get('sql_profile')!=resolved['profile']:
            raise SqlTrendError("The SQL schema/mapping changed; browse and reselect this point")
        rows=self._point_rows(conn,catalog,resolved,limit=2,point_id=point['point_id'])
        if len(rows)!=1:raise SqlTrendError("The selected point is missing or its metadata ID is not unique")
        actual=self.point_info(rows[0],resolved)
        if actual['name']!=point.get('expected_name') or actual['units']!=point.get('expected_units',''):
            raise SqlTrendError("The SQL point name or units changed; browse and review this meter")
    def history_page(self,point,start,end,after=None):
        with self.connection() as conn:
            catalog=self.catalog(conn);r=self.resolve(catalog);self.validate_point(conn,catalog,r,point)
            quality=None
            if r['profile']=='metasys':
                from_sql='[dbo].[tblActualValueFloat] t JOIN [dbo].[tblPointSlice] ps ON ps.[PointSliceID]=t.[PointSliceID]'
                where='ps.[PointID]=%s AND ps.[IsRawData]=1';stamp='t.[UTCDateTime]';value='t.[ActualValue]'
            else:
                m=self.settings.mapping;from_sql=table_ref(m.schema_name,m.table)+' t';where='t.'+quote(m.point_id_column)+'=%s'
                stamp='t.'+quote(m.timestamp_column);value='t.'+quote(m.value_column)
                if m.quality_column:quality='t.'+quote(m.quality_column)
            params=[]
            if quality:
                accepted=','.join(['%s']*len(self.settings.good_quality_values))
                quality_sql=f"MIN(CASE WHEN LOWER(LTRIM(RTRIM(CAST({quality} AS nvarchar(128))))) IN ({accepted}) THEN 1 ELSE 0 END)"
                params.extend(v.strip().lower() for v in self.settings.good_quality_values)
            else:quality_sql='NULL'
            def bound(v):
                v=sql_bound(v,r['timestamp_kind'],r['zone'],r['offset'])
                return v.isoformat() if isinstance(v,datetime) else v
            filters=where+f' AND {stamp}>=%s AND {stamp}<%s'
            params.extend([point['point_id'],bound(start),bound(end)])
            if after is not None:
                # The original SQL timestamp string is kept as the cursor, preserving SQL
                # datetime precision rather than round-tripping through a float epoch.
                filters+=f' AND {stamp}>%s';params.append(after)
            sql=f"SELECT TOP ({self.settings.batch_size+1}) {stamp} AS sample_time,MIN({value}) AS sample_value,MAX({value}) AS maximum_value,{quality_sql} AS accepted_quality,SUM(CASE WHEN {value} IS NULL THEN 1 ELSE 0 END) AS null_count FROM {from_sql} WHERE {filters} GROUP BY {stamp} ORDER BY {stamp}"
            rows=self.query(conn,sql,tuple(params));records=[]
            for row in rows[:self.settings.batch_size]:
                timestamp=sql_timestamp(row['sample_time'],r['timestamp_kind'],r['zone'])
                error=None;raw=row['sample_value']
                if raw is not None and row['maximum_value'] is not None and raw!=row['maximum_value']:
                    raise SqlTrendError("Conflicting remote values share a timestamp; resolve the source duplicate before importing")
                try:
                    if isinstance(raw,bool):raise ValueError()
                    numeric=float(raw)
                    if not math.isfinite(numeric):raise ValueError()
                except (ValueError,TypeError):numeric=0.;error="Remote value is NULL or non-finite; excluded from usage"
                if error:quality_value='fault'
                elif row['null_count']:quality_value='unverified';error='A duplicate remote sample has a NULL value'
                elif quality:quality_value='good' if row['accepted_quality']==1 else 'fault';error=None if quality_value=='good' else 'SQL quality flag is not an approved value'
                else:quality_value=self.settings.missing_quality;error=None if quality_value=='good' else 'SQL source has no mapped quality flag'
                records.append({'timestamp':timestamp,'value':numeric,'quality':quality_value,'error':error})
            last=rows[min(len(rows),self.settings.batch_size)-1]['sample_time'] if rows else None
            cursor=last.isoformat() if isinstance(last,datetime) else float(last) if last is not None else after
            return {'readings':records,'cursor':cursor,'more':len(rows)>self.settings.batch_size}
