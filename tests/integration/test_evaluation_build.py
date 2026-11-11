import json,runpy
from pathlib import Path
import pytest
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import create_engine,text
from fastapi.testclient import TestClient
from component_health import features,health,evaluation,ingestion
from component_health.ingestion import canonical,sha
from component_health.api import create_app
from component_health.settings import Settings

pytestmark=pytest.mark.integration


def full_synthetic_source(engine,analytics):
    factory=runpy.run_path('tests/test_detection.py')['fixture_rows']
    rows=factory(reference=196,development=[1.]*196,evaluation=[1.]*590+[1000.,1000.])
    inventory=ingestion.load_manifest(features.CONTRACTS/'source-file-inventory.json')
    ids={index:ingestion.recording_id(row['path']) for index,row in enumerate(inventory,1)}
    for row in rows['recordings']:
        row['recording_id']=ids[row['ordinal']];row['source_sha256']=inventory[row['ordinal']-1]['sha256']
    for row in rows['features']:
        row['recording_id']=ids[row['ordinal']];row['source_sha256']=inventory[row['ordinal']-1]['sha256']
    for row in rows['coverage']:row['recording_id']=ids[row['ordinal']]
    snapshot=dict(source_inventory_sha256=ingestion.INVENTORY_SHA,
        observations={row['accepted_receipt_id']:dict(receipt=dict(admission_status='ACCEPTED')) for row in rows['recordings']},
        disclosure='Synthetic feature values and receipt summaries for isolated protocol tests, not raw NASA measurements')
    snapshot_id=sha(canonical(snapshot));(analytics/'snapshots').mkdir();(analytics/'snapshots'/(snapshot_id+'.json')).write_bytes(canonical(snapshot))
    feature_id=sha(canonical(rows));directory=analytics/'feature-builds'/feature_id;directory.mkdir(parents=True)
    tables={}
    for name,schema in features.SCHEMAS.items():
        path=directory/(name+'.parquet');table=pa.Table.from_pylist(rows[name],schema=schema);pq.write_table(table,path,compression='zstd',version='2.6')
        tables[name]=dict(rows=table.num_rows,file_sha256=sha(path.read_bytes()),logical_sha256=features.logical_hash(table.to_pylist(),schema))
    manifest=dict(build_id=feature_id,stage='FEATURES_ONLY',snapshot_sha256=snapshot_id,tables=tables,
                  metrics=features.check_tables(directory,len(rows['recordings'])))
    (directory/'manifest.json').write_bytes(canonical(manifest));features.register(engine,manifest)
    return health.build(engine,analytics,feature_id)


def test_frozen_evaluation_rebuild_recovery_and_integrity(migrated_database,tmp_path):
    engine=create_engine(migrated_database);analytics=tmp_path/'analytics';analytics.mkdir();raw=tmp_path/'raw';raw.mkdir()
    development=full_synthetic_source(engine,analytics)
    baseline=(analytics/'health-builds'/development['build_id']/'baselines.json').read_bytes()
    plan_id=evaluation.freeze(engine,analytics,development['build_id'])
    assert evaluation.freeze(engine,analytics,development['build_id'])==plan_id
    with engine.connect() as connection: assert connection.scalar(text('SELECT count(*) FROM evaluation_builds'))==0
    def interrupt(_):raise RuntimeError('Controlled promotion/registry interruption')
    with pytest.raises(RuntimeError):evaluation.build(engine,analytics,plan_id,after_promotion=interrupt)
    first,report=evaluation.build(engine,analytics,plan_id);second,again=evaluation.build(engine,analytics,plan_id,recompute=True)
    assert first==second and report==again
    final=analytics/'evaluation-builds'/plan_id
    assert (final/'baselines.json').read_bytes()==baseline
    assert first['tables']['scores']['rows']==3152 and first['tables']['coverage']['rows']==3936
    assert report['operational']['evaluation']['all']['threshold_burden']==evaluation.ratio(8,2368)
    assert report['operational']['evaluation']['all']['open_at_boundary']==4
    assert report['source']['all']==evaluation.ratio(984,984)
    with TestClient(create_app(Settings(database_url=migrated_database,raw_directory=raw,analytical_directory=analytics))) as client:
        assert len(client.get('/api/v1/evaluations').json()['items'])==1
        assert client.get('/api/v1/evaluations?limit=101').status_code==422
    path=final/'metrics.json';original=path.read_bytes();path.write_bytes(b'corrupt')
    with pytest.raises(ValueError):evaluation.build(engine,analytics,plan_id)
    path.write_bytes(original)
    plan_path=analytics/'evaluation-plans'/(plan_id+'.json');original=plan_path.read_bytes();plan_path.write_bytes(original+b' ')
    with pytest.raises(ValueError,match='plan checksum'):evaluation.build(engine,analytics,plan_id)
    plan_path.write_bytes(original)
    with engine.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM evaluation_builds'))==1
        assert connection.scalar(text('SELECT p.frozen_at_utc <= b.registered_at_utc FROM evaluation_plans p JOIN evaluation_builds b USING(plan_id)'))
    engine.dispose()
