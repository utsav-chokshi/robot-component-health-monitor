import json
from pathlib import Path
import math
import pytest
import pyarrow.parquet as pq
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,text
from component_health.features import calculate, freeze, build, verify_bundle, definition_identity, CONFIG, logical_hash, SCHEMAS, CONTRACTS
from component_health.ingestion import ingest,sha
from component_health.api import create_app
from component_health.settings import Settings


@pytest.mark.parametrize('samples', [[3.,4.],[-3.,4.,-1.,2.],[0.,0.5,-0.25,0.75],list(range(-50,50))])
def test_independent_feature_oracle(samples):
    numbers,reasons=calculate(samples)
    n=len(samples);mean=math.fsum(samples)/n
    rms=math.sqrt(math.fsum(value*value for value in samples)/n)
    expected=dict(rms=rms,std_population=math.sqrt(math.fsum((value-mean)**2 for value in samples)/n),
                  absolute_peak=max(abs(value) for value in samples),crest_factor=max(abs(value) for value in samples)/rms)
    assert not reasons
    for name,value in expected.items(): assert numbers[name]==pytest.approx(value,rel=1e-9,abs=1e-12)


@pytest.mark.parametrize('samples,reason', [([0.,0.],'ZERO_RMS'),([2.,2.],'ZERO_VARIATION_CHANNEL'),
    ([1e308,-1e308],'NONFINITE_DERIVED'),([1e-300,-1e-300],'NUMERICAL_UNDERFLOW')])
def test_edge_cases_abstain(samples,reason):
    numbers,reasons=calculate(samples)
    assert reason in reasons
    assert all(value is None or math.isfinite(value) for value in numbers.values())
    if reason=='ZERO_RMS': assert numbers['crest_factor'] is None


@pytest.mark.parametrize('samples', [[],[1.],[float('nan'),1.],[float('inf'),1.]])
def test_invalid_kernel_inputs(samples):
    with pytest.raises(ValueError): calculate(samples)


def setup_inputs(tmp_path,engine):
    source=tmp_path/'source';source.mkdir();raw=tmp_path/'raw';raw.mkdir();analytics=tmp_path/'analytics';analytics.mkdir()
    inventory=[];split=[]
    for index,(filename,timestamp,partition,data) in enumerate([
        ('2004.02.12.10.32.39','2004-02-12T10:32:39','reference',b'3 1 -2 0.5\n4 2 -1 0.75\n'*10240),
        ('2004.02.12.10.42.39','2004-02-12T10:42:39','development',b'2 0 3 4\n'*20480),
        ('2004.02.12.10.52.39','2004-02-12T10:52:39','evaluation',b'1 2 3 4\n'*20480)],1):
        row=dict(path=filename,source_timestamp=timestamp,sha256=sha(data),bytes=len(data),samples=20480,channels=4)
        inventory.append(row)
        split.append(dict(ordinal=index,source_key='2nd_test/'+filename,source_sha256=sha(data),source_time=timestamp,
                          source_timezone='UNSPECIFIED',partition=partition,channels=[1,2,3,4]))
        if index!=3: (source/filename).write_bytes(data)
    ingest(engine,source,raw,inventory)
    return source,raw,analytics,inventory,split


@pytest.mark.integration
def test_frozen_rebuild_quality_lineage_and_registration_recovery(migrated_database,tmp_path):
    engine=create_engine(migrated_database)
    source,raw,analytics,inventory,split=setup_inputs(tmp_path,engine)
    snapshot=freeze(engine,raw,analytics,inventory,split)
    assert freeze(engine,raw,analytics,inventory,split)==snapshot
    def interrupt(_): raise RuntimeError('Controlled registry interruption')
    with pytest.raises(RuntimeError): build(engine,raw,analytics,snapshot,after_promotion=interrupt)
    with engine.connect() as connection: assert connection.scalar(text('SELECT count(*) FROM feature_builds'))==0
    first=build(engine,raw,analytics,snapshot)
    second=build(engine,raw,analytics,snapshot,recompute=True)
    assert first==second
    assert first['metrics']==dict(recordings=3,expected_channel_slots=12,feature_rows=8,eligible_feature_rows=4,
        unavailable_slots=4,findings=10,partition_slots=dict(reference=4,development=4,evaluation=4))
    directory=analytics/'feature-builds'/first['build_id']
    features=pq.read_table(directory/'features.parquet').to_pylist()
    assert features[0]['rms']==pytest.approx(math.sqrt(12.5))
    assert len({row['feature_id'] for row in features})==8
    assert all(row['sample_start']==0 and row['sample_stop_exclusive']==20480 for row in features)
    coverage=pq.read_table(directory/'coverage.parquet').to_pylist()
    assert all(not row['feature_eligible'] for row in coverage[4:])
    assert all(row['feature_id'] is None and row['reason_codes']==['MISSING_SOURCE'] for row in coverage[8:])
    with TestClient(create_app(Settings(database_url=migrated_database,raw_directory=raw,analytical_directory=analytics))) as client:
        assert len(client.get('/api/v1/feature-builds').json()['items'])==1
        assert client.get('/api/v1/feature-builds?limit=101').status_code==422
    original=(raw/'objects'/inventory[0]['sha256']).read_bytes()
    (raw/'objects'/inventory[0]['sha256']).write_bytes(b'corrupt')
    with pytest.raises(ValueError): build(engine,raw,analytics,snapshot)
    assert verify_bundle(directory)==first  # prior analytical bytes never overwritten
    (raw/'objects'/inventory[0]['sha256']).write_bytes(original)
    table_path=directory/'features.parquet';original_table=table_path.read_bytes();table_path.write_bytes(b'corrupt')
    with pytest.raises(ValueError): build(engine,raw,analytics,snapshot)
    table_path.write_bytes(original_table)
    assert verify_bundle(directory)==first
    # A later valid source changes the frozen version without mutating the old snapshot.
    (source/inventory[2]['path']).write_bytes(b'1 2 3 4\n'*20480)
    ingest(engine,source,raw,inventory)
    changed=freeze(engine,raw,analytics,inventory,split)
    assert changed!=snapshot
    third=build(engine,raw,analytics,changed)
    assert third['build_id']!=first['build_id'] and third['metrics']['feature_rows']==12
    assert build(engine,raw,analytics,snapshot,recompute=True)==first
    with pytest.raises(ValueError): build(engine,raw,analytics,snapshot,config=dict(CONFIG,std_ddof=1))
    bad_split=[dict(row) for row in split];bad_split[0]['source_time']='2000-01-01T00:00:00'
    with pytest.raises(ValueError): freeze(engine,raw,analytics,inventory,bad_split)
    engine.dispose()


def test_feature_definition_and_typed_logical_hash():
    contracts=CONTRACTS
    assert definition_identity(CONFIG,contracts)!=definition_identity(dict(CONFIG,std_ddof=1),contracts)
    assert logical_hash([],SCHEMAS['features'])!=logical_hash([],SCHEMAS['coverage'])


@pytest.mark.integration
@pytest.mark.parametrize('kind',['quarantined','conflict'])
def test_invalid_recording_has_coverage_without_measured_features(migrated_database,tmp_path,kind):
    engine=create_engine(migrated_database)
    source=tmp_path/'source';source.mkdir();raw=tmp_path/'raw';raw.mkdir();analytics=tmp_path/'analytics';analytics.mkdir()
    name='2004.02.12.10.32.39';data=b'NaN 2 3 4\n'*20480
    expected=data if kind=='quarantined' else b'1 2 3 4\n'*20480
    row=dict(path=name,source_timestamp='2004-02-12T10:32:39',sha256=sha(expected),bytes=len(expected),samples=20480,channels=4)
    (source/name).write_bytes(data)
    ingest(engine,source,raw,[row])
    slot=dict(ordinal=1,source_key='2nd_test/'+name,source_sha256=row['sha256'],source_time=row['source_timestamp'],
              source_timezone='UNSPECIFIED',partition='reference',channels=[1,2,3,4])
    snapshot=freeze(engine,raw,analytics,[row],[slot])
    manifest=build(engine,raw,analytics,snapshot)
    assert manifest['metrics']['feature_rows']==0 and manifest['metrics']['unavailable_slots']==4
    assert manifest['metrics']['eligible_feature_rows']==0
    coverage=pq.read_table(analytics/'feature-builds'/manifest['build_id']/'coverage.parquet').to_pylist()
    assert all(not item['feature_eligible'] and item['feature_id'] is None and 'NONFINITE_VALUE' in item['reason_codes'] for item in coverage)
    engine.dispose()
