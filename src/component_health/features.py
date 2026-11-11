"""Frozen, recording-local feature builds. Does not fit baselines or score health."""
import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import importlib.metadata
import inspect
import io
import json
import math
import os
from pathlib import Path
import platform
import resource
import tempfile
import time
import subprocess
import sys

import duckdb
from jsonschema import Draft202012Validator, FormatChecker
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text

from component_health import ingestion
from component_health.ingestion import canonical, sha, identity, recording_id, durable_write, bounded_read, validate_evidence

PROJECT_ROOT = Path(os.environ.get('RCHM_PROJECT_ROOT', '/app' if Path('/app/contracts/v1').is_dir() else str(Path.cwd())))
CONTRACTS = PROJECT_ROOT/'contracts/v1'
CONFIG = dict(version='time-domain-v1', dtype='float64', sample_start=0, sample_stop_exclusive=20480,
              features=['rms','std_population','absolute_peak','crest_factor'], std_ddof=0,
              amplitude_unit='native_numeric', crest_unit='dimensionless_ratio',
              source_timezone='UNSPECIFIED', nominal_sample_rate_hz=20000, timing_uncertainty=True,
              concatenate_recordings=False, overlap=0)


def calculate(values):
    """Numerical kernel accepts small fixtures; production caller enforces full windows."""
    x = np.asarray(values, dtype=np.float64)
    if x.ndim != 1 or x.size < 2 or not np.isfinite(x).all():
        raise ValueError('Expected at least two finite samples')
    reasons = []
    if np.all(x == x[0]): reasons.append('ZERO_VARIATION_CHANNEL')
    with np.errstate(over='ignore', invalid='ignore', divide='ignore', under='ignore'):
        rms = float(np.sqrt(np.mean(np.square(x), dtype=np.float64)))
        std = float(np.std(x, ddof=0, dtype=np.float64))
        peak = float(np.max(np.abs(x)))
        crest = peak / rms if rms > 0 else None
    if rms == 0:
        reasons.append('ZERO_RMS')
        if peak > 0: reasons.append('NUMERICAL_UNDERFLOW')
    if std == 0 and 'ZERO_VARIATION_CHANNEL' not in reasons:
        reasons.append('NUMERICAL_UNDERFLOW')
    result = dict(rms=rms, std_population=std, absolute_peak=peak, crest_factor=crest)
    for name,value in result.items():
        if value is not None and not math.isfinite(value):
            result[name] = None
            reasons.append('NONFINITE_DERIVED')
    if crest is not None and math.isfinite(crest) and crest < 1:
        reasons.append('INVALID_CREST_FACTOR')
    return result, sorted(set(reasons))


@contextmanager
def raw_read_lock(raw):
    # The receiver creates this file; a read-only mount can hold its shared lock.
    with (raw/'receiver.lock').open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        yield


def semantic(envelope):
    content = json.loads(canonical(envelope))
    content['receipt'].pop('received_at_utc')
    return content


def freeze(engine, raw, analytics, inventory, split):
    expected = sorted(inventory, key=lambda row: row['source_timestamp'])
    if len(split) != len(expected): raise ValueError('Split size mismatch')
    for ordinal,(row,slot) in enumerate(zip(expected,split),1):
        if (slot['ordinal'],slot['source_key'],slot['source_sha256'],slot['source_time'],slot['channels']) != (
                ordinal,'2nd_test/'+row['path'],row['sha256'],row['source_timestamp'],[1,2,3,4]):
            raise ValueError('Split/source relationship mismatch')
        if slot['partition'] not in {'reference','development','evaluation'} or slot['source_timezone']!='UNSPECIFIED':
            raise ValueError('Invalid split metadata')
    with raw_read_lock(raw), engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        catalog = connection.execute(text('SELECT recording_id,source_key,source_time,ordinal,expected_sha256,accepted_receipt_id FROM recordings ORDER BY ordinal')).all()
        projected = connection.execute(text('SELECT receipt_id,evidence FROM recording_receipts ORDER BY receipt_id')).all()
        if len(catalog) != len(expected): raise ValueError('Ingest/replay expected catalog first')
        raw_ids = {path.stem for path in (raw/'receipts').glob('*.json')}
        if raw_ids != {str(row.receipt_id) for row in projected}: raise ValueError('Unprojected or missing receipt; run replay')
        observations = {}
        for record in projected:
            path = raw/'receipts'/(str(record.receipt_id)+'.json')
            envelope = json.loads(path.read_bytes())
            if envelope != record.evidence: raise ValueError('Raw/database receipt mismatch')
            observations[str(record.receipt_id)] = semantic(envelope)
        rows = []
        for item,row,slot in zip(catalog,expected,split):
            if (str(item.recording_id),item.source_key,item.source_time.isoformat(),item.ordinal,item.expected_sha256) != (
                    recording_id(row['path']),'2nd_test/'+row['path'],row['source_timestamp'],slot['ordinal'],row['sha256']):
                raise ValueError('Catalog metadata mismatch')
            accepted = str(item.accepted_receipt_id) if item.accepted_receipt_id else None
            candidates = [key for key,value in observations.items() if value['receipt']['recording_id']==str(item.recording_id)]
            good = [key for key in candidates if observations[key]['receipt']['admission_status']=='ACCEPTED']
            if good != ([accepted] if accepted else []): raise ValueError('Accepted receipt relationship mismatch')
            rows.append(dict(inventory=row, split=slot, accepted_receipt_id=accepted, receipt_ids=sorted(candidates)))
    snapshot = dict(schema_version='1.0.0',stage='features',dataset_release=ingestion.RELEASE,
                    source_inventory_sha256=ingestion.INVENTORY_SHA,recordings=rows,observations=observations)
    digest = sha(canonical(snapshot))
    durable_write(analytics/'snapshots'/(digest+'.json'),canonical(snapshot))
    return digest


def definition_identity(config, contracts):
    return sha(canonical({'config':config,'feature_schema_sha256':sha((contracts/'schemas/feature-row.schema.json').read_bytes()),
                          'kernel_sha256':sha(inspect.getsource(calculate).encode()),'numpy_version':np.__version__}))


def implementation_identity():
    sources={name:sha(Path(module.__file__).read_bytes()) for name,module in
             [('features',__import__(__name__,fromlist=[''])),('ingestion',ingestion)]}
    sources['orchestration']=sha(Path(__file__).with_name('orchestration.py').read_bytes())
    return sha(canonical(sources))


def environment_identity():
    versions = {name:importlib.metadata.version(name) for name in ['numpy','pyarrow','duckdb','pydantic','SQLAlchemy']}
    return dict(python=platform.python_version(),platform='linux-amd64',versions=versions,
                locks={name:sha((PROJECT_ROOT/name).read_bytes()) for name in
                       ['requirements.lock','requirements-pipeline.lock','requirements-orchestration.lock']})


S = pa.string(); I = pa.int32(); F = pa.float64(); B = pa.bool_(); L = pa.list_(pa.field('element',S))
SCHEMAS = {
 'recordings':pa.schema([('recording_id',S),('source_key',S),('source_sha256',S),('accepted_receipt_id',S),
     ('source_time',S),('time_basis',S),('source_timezone',S),('ordinal',I),('partition',S),('amplitude_unit',S),('physical_unit_verified',B),('timing_uncertainty',B)]),
 'features':pa.schema([('schema_version',S),('feature_id',S),('recording_id',S),('channel_index',I),('source_sha256',S),
     ('ordinal',I),('partition',S),('sample_start',I),('sample_stop_exclusive',I),('feature_definition',S),
     ('amplitude_unit',S),('crest_unit',S),('rms',F),('std_population',F),('absolute_peak',F),('crest_factor',F),('quality_status',S),('reason_codes',L)]),
 'coverage':pa.schema([('slot_id',S),('recording_id',S),('component_id',S),('channel_index',I),('ordinal',I),('partition',S),
     ('expected_source_time',S),('source_timezone',S),('source_present',B),('source_admission',S),('feature_id',S),('feature_eligible',B),('reason_codes',L)]),
 'findings':pa.schema([('finding_id',S),('recording_id',S),('channel_index',I),('receipt_id',S),('reason_code',S),('scope',S)])
}
SORTS = {'recordings':lambda row:row['ordinal'],'features':lambda row:(row['ordinal'],row['channel_index']),
         'coverage':lambda row:(row['ordinal'],row['channel_index']),'findings':lambda row:row['finding_id']}


def logical_hash(rows, schema):
    def normalize(value):
        if isinstance(value,float):
            if not math.isfinite(value): raise ValueError('Nonfinite analytical value')
            return {'float64':(0.0 if value==0 else value).hex()}
        if isinstance(value,list): return [normalize(item) for item in value]
        if isinstance(value,dict): return {key:normalize(item) for key,item in value.items()}
        return value
    return sha(canonical({'columns':[(field.name,str(field.type)) for field in schema],'rows':normalize(rows)}))


def derive(snapshot, raw, definition, validator):
    result = {name:[] for name in SCHEMAS}
    def finding(recording, channel, receipt, reason, scope):
        item = dict(recording_id=recording,channel_index=channel,receipt_id=receipt,reason_code=reason,scope=scope)
        result['findings'].append(dict(finding_id=identity(item),**item))
    for selected in snapshot['recordings']:
        row,slot = selected['inventory'],selected['split']
        rid = recording_id(row['path']); accepted = selected['accepted_receipt_id']
        values = None; unavailable = []
        for receipt_id in selected['receipt_ids']:
            path = raw/'receipts'/(receipt_id+'.json')
            envelope = json.loads(path.read_bytes())
            if semantic(envelope) != snapshot['observations'][receipt_id]: raise ValueError('Frozen receipt changed')
            validate_evidence(envelope,row,raw)
            receipt = envelope['receipt']
            for reason in receipt['reason_codes']: finding(rid,None,receipt_id,reason,'SOURCE')
            if receipt_id==accepted:
                if receipt['admission_status']!='ACCEPTED': raise ValueError('Nonaccepted selected source')
                data,_,reasons = bounded_read(raw/'objects',row['sha256'])
                if reasons or sha(data)!=row['sha256']: raise ValueError('Source integrity failure')
                values = np.loadtxt(io.BytesIO(data),dtype=np.float64,ndmin=2)
                if values.shape!=(20480,4) or not np.isfinite(values).all(): raise ValueError('Invalid preserved shape')
        if accepted is None:
            unavailable = sorted({reason for receipt_id in selected['receipt_ids']
                for reason in snapshot['observations'][receipt_id]['receipt']['reason_codes']}) or ['MISSING_SOURCE']
        result['recordings'].append(dict(recording_id=rid,source_key=slot['source_key'],source_sha256=row['sha256'] if accepted else None,
            accepted_receipt_id=accepted,source_time=slot['source_time'],time_basis='OBSERVED_FILENAME' if accepted else 'EXPECTED_MANIFEST',source_timezone='UNSPECIFIED',ordinal=slot['ordinal'],partition=slot['partition'],
            amplitude_unit='native_numeric',physical_unit_verified=False,timing_uncertainty=True))
        for channel in range(1,5):
            feature = None; reasons = list(unavailable)
            if values is not None:
                numbers,reasons = calculate(values[:,channel-1])
                fid = identity(dict(recording_id=rid,channel_index=channel,sample_start=0,sample_stop_exclusive=20480,feature_definition_sha256=definition))
                feature = dict(schema_version='1.0.0',feature_id=fid,recording_id=rid,channel_index=channel,source_sha256=row['sha256'],
                    ordinal=slot['ordinal'],partition=slot['partition'],sample_start=0,sample_stop_exclusive=20480,
                    feature_definition='time-domain-v1',amplitude_unit='native_numeric',crest_unit='dimensionless_ratio',
                    **numbers,quality_status='UNSCORABLE' if reasons else 'VALID',reason_codes=reasons)
                validator.validate(feature)
                result['features'].append(feature)
            for reason in reasons: finding(rid,channel,accepted,reason,'CHANNEL' if values is not None else 'COVERAGE')
            result['coverage'].append(dict(slot_id=identity(dict(recording_id=rid,channel_index=channel)),recording_id=rid,
                component_id=identity(dict(dataset_release=ingestion.RELEASE,experiment='ims-test-2',channel_index=channel)),channel_index=channel,
                ordinal=slot['ordinal'],partition=slot['partition'],expected_source_time=slot['source_time'],source_timezone='UNSPECIFIED',
                source_present=bool(accepted or any(snapshot['observations'][key]['receipt']['raw_preserved'] for key in selected['receipt_ids'])),
                source_admission='ACCEPTED' if accepted else 'UNAVAILABLE',feature_id=feature['feature_id'] if feature else None,
                feature_eligible=feature is not None and not reasons,reason_codes=reasons))
    for name in result: result[name].sort(key=SORTS[name])
    return result


def check_tables(directory, expected_recordings):
    with duckdb.connect(':memory:') as db:
        db.execute("SET threads=1")
        db.execute("SET memory_limit='256MB'")
        for name in SCHEMAS:
            db.from_parquet(str(directory/(name+'.parquet'))).create_view(name)
        for name,key in [('recordings','recording_id'),('features','feature_id'),('coverage','slot_id'),('findings','finding_id')]:
            if db.execute(f'SELECT count(*)-count(DISTINCT {key}) FROM {name}').fetchone()[0]: raise ValueError('Duplicate analytical identities')
        if db.execute('SELECT count(*) FROM recordings').fetchone()[0]!=expected_recordings: raise ValueError('Recording denominator mismatch')
        if db.execute('SELECT count(*) FROM coverage').fetchone()[0]!=expected_recordings*4: raise ValueError('Coverage denominator mismatch')
        if db.execute('SELECT count(*) FROM features f LEFT JOIN recordings r USING(recording_id) WHERE r.recording_id IS NULL OR f.source_sha256<>r.source_sha256').fetchone()[0]: raise ValueError('Feature lineage mismatch')
        if db.execute('''SELECT count(*) FROM (SELECT recording_id FROM coverage GROUP BY recording_id
                HAVING count(*)<>4 OR count(DISTINCT channel_index)<>4 OR min(channel_index)<>1 OR max(channel_index)<>4)''').fetchone()[0]: raise ValueError('Coverage channel layout mismatch')
        if db.execute('''SELECT count(*) FROM coverage c LEFT JOIN recordings r USING(recording_id)
                WHERE r.recording_id IS NULL OR c.ordinal<>r.ordinal OR c.partition<>r.partition OR c.expected_source_time<>r.source_time''').fetchone()[0]: raise ValueError('Coverage lineage mismatch')
        if db.execute('''SELECT count(*) FROM features f LEFT JOIN coverage c USING(feature_id)
                WHERE c.feature_id IS NULL OR f.recording_id<>c.recording_id OR f.channel_index<>c.channel_index
                OR f.ordinal<>c.ordinal OR f.partition<>c.partition OR f.sample_start<>0 OR f.sample_stop_exclusive<>20480''').fetchone()[0]: raise ValueError('Feature window/coverage mismatch')
        if db.execute('''SELECT count(*) FROM coverage c LEFT JOIN features f USING(feature_id)
                WHERE (c.feature_id IS NOT NULL AND f.feature_id IS NULL) OR c.feature_eligible IS DISTINCT FROM (f.quality_status='VALID' AND f.feature_id IS NOT NULL)''').fetchone()[0]: raise ValueError('Coverage/eligibility mismatch')
        return {'recordings':expected_recordings,'expected_channel_slots':expected_recordings*4,
                'feature_rows':db.execute('SELECT count(*) FROM features').fetchone()[0],
                'eligible_feature_rows':db.execute("SELECT count(*) FROM features WHERE quality_status='VALID'").fetchone()[0],
                'unavailable_slots':db.execute('SELECT count(*) FROM coverage WHERE feature_id IS NULL').fetchone()[0],
                'findings':db.execute('SELECT count(*) FROM findings').fetchone()[0],
                'partition_slots':dict(db.execute('SELECT partition,count(*) FROM coverage GROUP BY partition ORDER BY partition').fetchall())}


def verify_bundle(directory):
    manifest = json.loads((directory/'manifest.json').read_bytes())
    for name,info in manifest['tables'].items():
        if name not in SCHEMAS: raise ValueError('Unexpected table')
        path = directory/(name+'.parquet')
        if sha(path.read_bytes())!=info['file_sha256']: raise ValueError('Analytical file checksum mismatch')
        table = pq.read_table(path)
        rows = table.to_pylist()
        if table.schema!=SCHEMAS[name] or len(rows)!=info['rows'] or logical_hash(rows,table.schema)!=info['logical_sha256']:
            raise ValueError('Analytical logical/schema mismatch')
    if set(manifest['tables'])!=set(SCHEMAS): raise ValueError('Incomplete feature bundle')
    if check_tables(directory,manifest['metrics']['recordings'])!=manifest['metrics']: raise ValueError('Bundle metrics mismatch')
    return manifest


def register(engine, manifest):
    payload=canonical(manifest).decode(); digest=sha(payload.encode())
    with engine.begin() as connection:
        connection.execute(text('''INSERT INTO feature_builds(build_id,snapshot_sha256,manifest_sha256,manifest)
            VALUES(:id,:snapshot,:sha,CAST(:manifest AS jsonb)) ON CONFLICT (build_id) DO NOTHING'''),
            dict(id=manifest['build_id'],snapshot=manifest['snapshot_sha256'],sha=digest,manifest=payload))
        existing=connection.scalar(text('SELECT manifest_sha256 FROM feature_builds WHERE build_id=:id'),{'id':manifest['build_id']})
        if existing!=digest: raise ValueError('Registry manifest mismatch')


def build(engine, raw, analytics, snapshot_id, *, contracts=CONTRACTS, config=None, recompute=False, after_promotion=None):
    if len(snapshot_id)!=64 or any(c not in '0123456789abcdef' for c in snapshot_id): raise ValueError('Invalid snapshot ID')
    data=(analytics/'snapshots'/(snapshot_id+'.json')).read_bytes()
    if sha(data)!=snapshot_id: raise ValueError('Frozen snapshot mismatch')
    snapshot=json.loads(data); config=config or CONFIG
    if config!=CONFIG: raise ValueError('Unsupported feature configuration; implement/version explicitly')
    definition=definition_identity(config,contracts)
    provenance=dict(snapshot_sha256=snapshot_id,feature_definition_sha256=definition,
                    implementation_sha256=implementation_identity(),environment=environment_identity())
    build_id=sha(canonical(provenance)); destination=analytics/'feature-builds'/build_id
    analytics.mkdir(parents=True,exist_ok=True)
    with (analytics/'feature-build.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if destination.exists() and not recompute:
            for selected in snapshot['recordings']:
                for receipt_id in selected['receipt_ids']:
                    envelope=json.loads((raw/'receipts'/(receipt_id+'.json')).read_bytes())
                    if semantic(envelope)!=snapshot['observations'][receipt_id]: raise ValueError('Frozen receipt changed')
                    validate_evidence(envelope,selected['inventory'],raw)
            manifest=verify_bundle(destination)
            if manifest['build_id']!=build_id or manifest['provenance']!=provenance: raise ValueError('Build identity mismatch')
            register(engine,manifest)
            return manifest
        validator=Draft202012Validator(json.loads((contracts/'schemas/feature-row.schema.json').read_bytes()),format_checker=FormatChecker())
        rows=derive(snapshot,raw,definition,validator)
        staging=analytics/'.staging'; staging.mkdir(exist_ok=True)
        destination.parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='features-',dir=staging) as temporary:
            temporary=Path(temporary); tables={}
            for name,schema in SCHEMAS.items():
                path=temporary/(name+'.parquet'); table=pa.Table.from_pylist(rows[name],schema=schema)
                pq.write_table(table,path,compression='zstd',version='2.6',write_statistics=True)
                with path.open('rb') as stream: os.fsync(stream.fileno())
                tables[name]=dict(rows=table.num_rows,file_sha256=sha(path.read_bytes()),logical_sha256=logical_hash(table.to_pylist(),schema))
            metrics=check_tables(temporary,len(snapshot['recordings']))
            manifest=dict(schema_version='1.0.0',stage='FEATURES_ONLY',dataset_release=ingestion.RELEASE,experiment='ims-test-2',build_id=build_id,snapshot_sha256=snapshot_id,
                provenance=provenance,configuration=config,tables=tables,metrics=metrics)
            durable_write(temporary/'manifest.json',canonical(manifest))
            verify_bundle(temporary)
            if destination.exists():
                if verify_bundle(destination)!=manifest: raise ValueError('Rebuild content differs')
            else:
                os.rename(temporary,destination)
                # TemporaryDirectory cleanup tolerates the promoted path being absent.
                parent=os.open(destination.parent,os.O_DIRECTORY)
                try: os.fsync(parent)
                finally: os.close(parent)
        if after_promotion: after_promotion(manifest)
        register(engine,manifest)
        return manifest


def load_split(contracts,inventory):
    document=json.loads((contracts/'split-manifest-v1.json').read_bytes())
    if document['source_inventory_sha256']!=ingestion.INVENTORY_SHA: raise ValueError('Split inventory mismatch')
    rows=document['recordings']
    if len(rows)!=984 or Counter(row['partition'] for row in rows)!=dict(reference=196,development=196,evaluation=592): raise ValueError('Split counts mismatch')
    for row in rows:
        expected='reference' if row['ordinal']<=196 else 'development' if row['ordinal']<=392 else 'evaluation'
        if row['partition']!=expected: raise ValueError('Chronological partition mismatch')
    return rows


def run_pipeline(engine,raw,analytics,contracts, snapshot_id=None,recompute=False):
    if snapshot_id is None:
        inventory=ingestion.load_manifest(contracts/'source-file-inventory.json')
        snapshot_id=freeze(engine,raw,analytics,inventory,load_split(contracts,inventory))
    return build(engine,raw,analytics,snapshot_id,contracts=contracts,recompute=recompute)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot');parser.add_argument('--recompute',action='store_true');parser.add_argument('--orchestrated',action='store_true')
    args=parser.parse_args()
    if args.orchestrated:
        executable=os.environ.get('RCHM_ORCHESTRATOR_PYTHON','/opt/orchestrator/bin/python')
        command=[executable,str(Path(__file__).with_name('orchestration.py')),sys.executable,'-m','component_health.features']
        if args.snapshot: command+=['--snapshot',args.snapshot]
        if args.recompute: command+=['--recompute']
        raise SystemExit(subprocess.call(command))
    from component_health.database import build_engine
    from component_health.settings import Settings
    settings=Settings(); engine=build_engine(settings); started=time.perf_counter()
    try:
        manifest=run_pipeline(engine,settings.raw_directory,settings.analytical_directory,CONTRACTS,args.snapshot,args.recompute)
        print(json.dumps(dict(build_id=manifest['build_id'],snapshot_sha256=manifest['snapshot_sha256'],metrics=manifest['metrics'],
            elapsed_seconds=round(time.perf_counter()-started,3),worker_peak_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,3)),sort_keys=True))
    except Exception:
        parser.exit(1,'Feature build failed; no valid feature bundle registered. Check frozen inputs and integrity.\n')
    finally: engine.dispose()


if __name__=='__main__': main()
