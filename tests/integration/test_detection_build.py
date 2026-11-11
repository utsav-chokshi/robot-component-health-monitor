import json,runpy
import pytest
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import create_engine,text
from fastapi.testclient import TestClient
from component_health import features,health
from component_health.ingestion import sha,canonical
from component_health.api import create_app
from component_health.settings import Settings

pytestmark=pytest.mark.integration


def fixture_bundle(engine,analytics):
    factory=runpy.run_path('tests/test_detection.py')['fixture_rows']
    rows=factory(development=(100.,100.,1.,1.,1.,100.,100.))
    build_id=sha(canonical(rows));directory=analytics/'feature-builds'/build_id;directory.mkdir(parents=True)
    tables={}
    for name,schema in features.SCHEMAS.items():
        table=pa.Table.from_pylist(rows[name],schema=schema);path=directory/(name+'.parquet');pq.write_table(table,path,compression='zstd',version='2.6')
        tables[name]=dict(rows=table.num_rows,file_sha256=sha(path.read_bytes()),logical_sha256=features.logical_hash(table.to_pylist(),schema))
    manifest=dict(build_id=build_id,snapshot_sha256='a'*64,stage='FEATURES_ONLY',tables=tables,metrics=features.check_tables(directory,len(rows['recordings'])))
    (directory/'manifest.json').write_bytes(canonical(manifest));features.register(engine,manifest)
    return build_id


def test_health_rebuild_recovery_corruption_and_api(migrated_database,tmp_path,monkeypatch):
    engine=create_engine(migrated_database);analytics=tmp_path/'analytics';analytics.mkdir();raw=tmp_path/'raw';raw.mkdir()
    feature_id=fixture_bundle(engine,analytics)
    def interrupt(_): raise RuntimeError('Controlled interruption after health promotion')
    with pytest.raises(RuntimeError): health.build(engine,analytics,feature_id,after_promotion=interrupt)
    with engine.connect() as connection: assert connection.scalar(text('SELECT count(*) FROM health_builds'))==0
    first=health.build(engine,analytics,feature_id);second=health.build(engine,analytics,feature_id,recompute=True)
    assert first==second
    assert first['metrics']['score_slots']==28 and first['metrics']['scorable']==28
    assert first['metrics']['alerts']==8 and first['metrics']['closed_alerts']==4 and first['metrics']['open_boundary_alerts']==4
    assert first['metrics']['evaluation_scored']==0 and first['metrics']['evaluation_reserved_slots']==4
    directory=analytics/'health-builds'/first['build_id']
    assert health.verify_health(directory)==first
    with TestClient(create_app(Settings(database_url=migrated_database,raw_directory=raw,analytical_directory=analytics))) as client:
        result=client.get('/api/v1/health-builds').json()
        assert result['evaluation_status']=='NOT_EVALUATED' and len(result['items'])==1
        assert client.get('/api/v1/health-builds?limit=101').status_code==422
        assert client.post('/api/v1/maintenance',json={}).status_code==404
    path=directory/'baselines.json';original=path.read_bytes();path.write_bytes(b'corrupt')
    with pytest.raises(ValueError): health.build(engine,analytics,feature_id)
    path.write_bytes(original)
    source=analytics/'feature-builds'/feature_id/'features.parquet';original=source.read_bytes();source.write_bytes(b'corrupt')
    with pytest.raises(ValueError): health.build(engine,analytics,feature_id,recompute=True)
    source.write_bytes(original)
    assert health.verify_health(directory)==first
    with engine.connect() as connection: assert connection.scalar(text('SELECT count(*) FROM health_builds'))==1
    config=json.loads((features.CONTRACTS/'condition-monitoring-config.json').read_bytes());config['baseline']['location']='mean_rms'
    modified=tmp_path/'modified-contracts';modified.mkdir();(modified/'condition-monitoring-config.json').write_text(json.dumps(config))
    with monkeypatch.context() as changed:
        changed.setattr(features,'CONTRACTS',modified)
        with pytest.raises(ValueError,match='Unsupported monitoring configuration'): health.build(engine,analytics,feature_id)
    engine.dispose()
