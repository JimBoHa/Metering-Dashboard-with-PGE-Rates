from datetime import datetime, timezone
import pytest
from metering.sql_server import SqlReader,SqlTrendError,quote,like_filter,sql_timestamp
from metering.sql_models import SqlSettings
from sql_fixture import SyntheticSql, settings, mapping

@pytest.fixture
def reader(tmp_path):
    db=SyntheticSql();return SqlReader(settings(),'synthetic-sql-password',tmp_path,db.connect),db

def test_tls_readonly_and_password_never_in_query(reader):
    r,db=reader;r.inspect();args=db.connect_calls[0]
    assert args['readonly'] and args['autocommit'] and args['validate_host']
    assert args['cafile'] and not args['enc_login_only']
    assert all('synthetic-sql-password' not in query for query,params in db.calls)

def test_failed_driver_does_not_disclose_secrets(reader):
    r,db=reader;db.failed=True
    with pytest.raises(SqlTrendError) as e:r.inspect()
    assert 'synthetic-password-from-driver' not in str(e.value)

def test_metasys_tree_metadata_and_original_times(reader):
    r,db=reader;inspection=r.inspect();assert inspection['profile']=='metasys' and inspection['ready']
    points=r.points();assert points['points'][0]['path'].startswith('Demo/Building A/')
    p=points['points'][0]['point'];page=r.history_page(p,datetime(2026,11,1,tzinfo=timezone.utc).timestamp(),datetime(2026,11,2,tzinfo=timezone.utc).timestamp())
    assert len(page['readings'])==3
    assert page['readings'][0]['timestamp']==datetime(2026,11,1,12,tzinfo=timezone.utc).timestamp()
    assert page['readings'][0]['quality']=='good'
    assert any('ps.[IsRawData]=1' in q for q,_ in db.calls)

def test_source_without_quality_defaults_unverified(tmp_path):
    db=SyntheticSql();r=SqlReader(settings(missing_quality='unverified'),'synthetic',tmp_path,db.connect)
    point=r.points()['points'][0]['point'];page=r.history_page(point,1793491200,1793577600)
    assert page['readings'][0]['quality']=='unverified'

def test_custom_mapping_and_bad_quality(tmp_path):
    db=SyntheticSql();db.quality=0;r=SqlReader(settings(profile='custom',mapping=mapping()),'synthetic',tmp_path,db.connect)
    point=r.points()['points'][0]['point'];page=r.history_page(point,1793491200,1793577600)
    assert page['readings'][0]['quality']=='fault'
    sql,params=db.calls[-1];assert params[:2]==('good','0');assert '[reporting].[Samples]' in sql

def test_parameterized_point_search_and_identifier_escaping(reader):
    r,db=reader;r.points("%_' OR 1=1 --")
    sql,params=db.calls[-1];assert "OR 1=1 --" not in sql
    assert params[0]==like_filter("%_' OR 1=1 --")
    assert quote('a]b')=='[a]]b]'
    with pytest.raises(SqlTrendError):quote('')

def test_missing_or_changed_mapping_rejected(reader):
    r,db=reader;point=r.points()['points'][0]['point'];point['mapping_signature']='changed'
    with pytest.raises(SqlTrendError,match='mapping changed'):r.history_page(point,1793491200,1793577600)

def test_conflicting_remote_samples_are_rejected(reader):
    r,db=reader;point=r.points()['points'][0]['point'];db.conflicting=True
    with pytest.raises(SqlTrendError,match='Conflicting remote'):r.history_page(point,1793491200,1793577600)

@pytest.mark.parametrize('value',[datetime(2026,11,1,1,30),datetime(2026,3,8,2,30)])
def test_ambiguous_or_nonexistent_local_dates_rejected(value):
    with pytest.raises(SqlTrendError,match='ambiguous or nonexistent'):sql_timestamp(value,'datetime','America/Los_Angeles')

def test_epoch_milliseconds():assert sql_timestamp(1700000000123,'unix_milliseconds','UTC')==1700000000.123

def test_custom_mapping_needs_numeric_value(tmp_path):
    db=SyntheticSql();m=mapping();m['value_column']='Quality'
    r=SqlReader(settings(profile='custom',mapping=m),'synthetic',tmp_path,db.connect)
    with pytest.raises(SqlTrendError,match='numeric SQL type'):r.points()
