"""Freeze the prior development result, then evaluate without fitting or tuning."""
import argparse
from collections import Counter
from datetime import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import resource
import tempfile
import time

from jsonschema import Draft202012Validator,FormatChecker
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text
from component_health import features,health,ingestion
from component_health.ingestion import canonical,sha,durable_write,identity


def valid_id(value):
    if len(value)!=64 or any(char not in '0123456789abcdef' for char in value): raise ValueError('Invalid artifact ID')
    return value


def ratio(numerator,denominator):
    return dict(numerator=numerator,denominator=denominator,value=numerator/denominator if denominator else None,
                status='AVAILABLE' if denominator else 'NOT_APPLICABLE')


def read_rows(directory):
    return {name:pq.read_table(directory/(name+'.parquet')).to_pylist() for name in health.SCHEMAS}


def load_inputs(engine,analytics,development_id):
    development_id=valid_id(development_id);directory=analytics/'health-builds'/development_id
    manifest=health.verify_health(directory)
    with engine.connect() as connection:
        registered=connection.scalar(text('SELECT manifest_sha256 FROM health_builds WHERE build_id=:id'),{'id':development_id})
    if registered!=sha(canonical(manifest)) or manifest['stage']!='DEVELOPMENT_HEALTH' or manifest['build_id']!=development_id: raise ValueError('Development registration mismatch')
    source=analytics/'feature-builds'/manifest['feature_build_id'];feature_manifest=features.verify_bundle(source)
    if sha(canonical(feature_manifest))!=manifest['provenance']['feature_manifest_sha256']: raise ValueError('Feature input changed')
    baseline=(directory/'baselines.json').read_bytes()
    if sha(baseline)!=manifest['baselines_sha256'] or manifest['provenance']['policy_sha256']!=health.policy_digest(): raise ValueError('Frozen baseline/policy mismatch')
    if sha((features.CONTRACTS/'condition-monitoring-config.json').read_bytes())!=manifest['provenance']['configuration_sha256']: raise ValueError('Configuration changed')
    return directory,manifest,source,feature_manifest,baseline


def freeze(engine,analytics,development_id):
    directory,development,source,feature_manifest,baseline=load_inputs(engine,analytics,development_id)
    inventory=ingestion.load_manifest(features.CONTRACTS/'source-file-inventory.json')
    split=features.load_split(features.CONTRACTS,inventory)
    records=pq.read_table(source/'recordings.parquet').to_pylist();coverage=pq.read_table(source/'coverage.parquet').to_pylist()
    if len(records)!=984 or len(coverage)!=3936: raise ValueError('Expected full source coverage')
    for row,expected,slot in zip(records,inventory,split):
        if row['recording_id']!=ingestion.recording_id(expected['path']): raise ValueError('Recording identity mismatch')
        if (row['ordinal'],row['source_key'],row['source_time'],row['partition'])!=(slot['ordinal'],slot['source_key'],slot['source_time'],slot['partition']): raise ValueError('Frozen split membership mismatch')
        if row['source_sha256'] is not None and row['source_sha256']!=expected['sha256']: raise ValueError('Source release mismatch')
    snapshot_id=valid_id(feature_manifest['snapshot_sha256']);snapshot_bytes=(analytics/'snapshots'/(snapshot_id+'.json')).read_bytes()
    if sha(snapshot_bytes)!=snapshot_id: raise ValueError('Receipt snapshot changed')
    snapshot=json.loads(snapshot_bytes)
    if snapshot['source_inventory_sha256']!=ingestion.INVENTORY_SHA: raise ValueError('Receipt inventory mismatch')
    definitions_bytes=(features.CONTRACTS/'evaluation-metrics-v1.json').read_bytes();definitions=json.loads(definitions_bytes)
    if definitions['version']!='ims2-evaluation-metrics-v1' or definitions['partitions']!=['development','evaluation']: raise ValueError('Unsupported metrics version')
    document=dict(schema_version='1.0.0',stage='FROZEN_EVALUATION_PLAN',development_build_id=development_id,
        development_manifest_sha256=sha(canonical(development)),feature_build_id=development['feature_build_id'],
        feature_manifest_sha256=sha(canonical(feature_manifest)),receipt_snapshot_sha256=snapshot_id,
        source_inventory_sha256=ingestion.INVENTORY_SHA,split_sha256=sha((features.CONTRACTS/'split-manifest-v1.json').read_bytes()),
        baselines_sha256=sha(baseline),policy_sha256=development['provenance']['policy_sha256'],
        configuration_sha256=development['provenance']['configuration_sha256'],metric_definitions=definitions,metric_definition_sha256=sha(definitions_bytes),
        implementation_sha256=sha(Path(__file__).read_bytes()),environment=features.environment_identity(),
        reference_recordings=196,development_recordings=196,evaluation_recordings=592,
        disclosure='Single IMS experiment; previously inspected for feasibility, not a blind or unseen-component test')
    plan_id=sha(canonical(document));durable_write(analytics/'evaluation-plans'/(plan_id+'.json'),canonical(document))
    with engine.begin() as connection:
        connection.execute(text('''INSERT INTO evaluation_plans(plan_id,health_build_id,document)
            VALUES(:id,:health,CAST(:document AS jsonb)) ON CONFLICT(plan_id) DO NOTHING'''),
            dict(id=plan_id,health=development_id,document=canonical(document).decode()))
        if connection.scalar(text('SELECT document FROM evaluation_plans WHERE plan_id=:id'),{'id':plan_id})!=document: raise ValueError('Plan registry mismatch')
    return plan_id


def derive(input_rows,baselines,policy_sha,development_rows):
    # Reuse parameter bytes and policy identity. Never call a fit function here.
    scores=health.score_slots(input_rows['coverage'],input_rows['features'],baselines,('development','evaluation'),policy_sha)
    validator=Draft202012Validator(json.loads((features.CONTRACTS/'schemas/score-row.schema.json').read_bytes()),format_checker=FormatChecker())
    for score in scores: validator.validate(score)
    alerts,evidence,states=health.alert_lifecycle(scores,input_rows['coverage'],policy_sha)
    coverage=[]
    for slot in input_rows['coverage']:
        if slot['partition']=='reference':
            state=dict(score_id=None,score_status='NOT_APPLICABLE',score_reason_codes=['REFERENCE_FIT_ONLY'],active_alert_id=None,
                observed_state='REFERENCE_FIT_ONLY',entry_streak=0,clear_streak=0,coverage_uncertain=not slot['feature_eligible'],transition='NONE')
        else: state=states[slot['slot_id']]
        coverage.append(dict(**slot,**state))
    findings=list(input_rows['findings']);slots={slot['slot_id']:slot for slot in coverage}
    for score in scores:
        slot=slots[score['window_id']]
        for reason in score['reason_codes']:
            finding=dict(recording_id=slot['recording_id'],channel_index=slot['channel_index'],receipt_id=None,reason_code=reason,scope='SCORING')
            findings.append(dict(finding_id=identity(finding),**finding))
    rows={**input_rows,'coverage':coverage,'scores':scores,
          'alerts':sorted(alerts,key=lambda row:row['alert_id']),'alert_evidence':sorted(evidence,key=lambda row:row['evidence_id']),
          'findings':sorted(findings,key=lambda row:row['finding_id'])}
    # Preserve prior development scores, events and causal coverage states exactly.
    for name in ['scores','alerts','coverage']:
        if [row for row in rows[name] if row['partition']=='development']!=[row for row in development_rows[name] if row['partition']=='development']:
            raise ValueError('Development result changed')
    old_alerts={row['alert_id'] for row in development_rows['alerts']}
    if [row for row in rows['alert_evidence'] if row['alert_id'] in old_alerts]!=development_rows['alert_evidence']: raise ValueError('Development evidence changed')
    return rows


def metrics(rows,snapshot,definitions,baselines):
    result=dict(version=definitions['version'],source={},features={},operational={},reference_assumption='ASSUMED_NOT_LABELED')
    result['source']['all']=ratio(sum(row['accepted_receipt_id'] is not None for row in rows['recordings']),len(rows['recordings']))
    result['features']['all']=ratio(sum(row['feature_eligible'] for row in rows['coverage']),len(rows['coverage']))
    result['baselines']=[dict(baseline_id=row['baseline_id'],channel_index=row['channel_index'],status=row['status'],
        declared_reference_slots=row['declared_reference_slots'],used_reference_slots=len(row['used']),excluded_reference_slots=len(row['excluded']),
        reason_codes=row['reason_codes']) for row in baselines]
    for partition in ['reference','development','evaluation']:
        records=[row for row in rows['recordings'] if row['partition']==partition]
        slots=[row for row in rows['coverage'] if row['partition']==partition]
        result['source'][partition]=ratio(sum(row['accepted_receipt_id'] is not None for row in records),len(records))
        result['features'][partition]=ratio(sum(row['feature_eligible'] for row in slots),len(slots))
        if partition=='reference': continue
        groups={}
        for channel in [None,1,2,3,4]:
            selected=[row for row in rows['scores'] if row['partition']==partition and (channel is None or row['channel_index']==channel)]
            declared=[row for row in slots if channel is None or row['channel_index']==channel]
            valid=[row for row in selected if row['status']=='SCORABLE']
            alerts=[row for row in rows['alerts'] if row['partition']==partition and (channel is None or row['channel_index']==channel)]
            ids={row['alert_id'] for row in alerts}
            last={row['channel_index']:row for row in sorted(declared,key=lambda row:row['ordinal'])}
            groups['all' if channel is None else str(channel)]=dict(declared_slots=len(declared),score_rows=len(selected),
                score_coverage=ratio(len(valid),len(declared)),unscorable_slots=len(selected)-len(valid),
                exclusion_reasons=dict(sorted(Counter(reason for row in selected for reason in row['reason_codes']).items())),
                threshold_burden=ratio(sum(row['value']>=6 for row in valid),len(valid)),
                active_window_burden=ratio(sum(row['score_status']=='SCORABLE' and row['active_alert_id'] is not None for row in declared),len(valid)),
                alerts=len(alerts),closed=sum(row['state']=='CLOSED' for row in alerts),open_at_boundary=sum(row['state']=='OPEN_AT_BOUNDARY' for row in alerts),
                interrupted_coverage=sum(row['coverage_interrupted'] for row in alerts),evidence_rows=sum(row['alert_id'] in ids for row in rows['alert_evidence']),
                pending_entry_groups=sum(row['entry_streak']>0 for row in last.values()))
        result['operational'][partition]=groups
    result['receipt_admission_observations']=dict(sorted(Counter(value['receipt']['admission_status'] for value in snapshot['observations'].values()).items()))
    result['duplicate_delivery_attempts']=dict(status='NOT_MEASURED',value=None,reason=definitions['duplicate_delivery_attempts'])
    result['unsupported']={name:dict(status='NOT_APPLICABLE',value=None,reason=definitions['unsupported_reason']) for name in definitions['unsupported']}
    result['detector_suitability']=dict(status='NOT_ESTABLISHED',reason='Excursions and alert episodes are observable; reliable fault/healthy labels and timing are unavailable')
    return result


def verify(directory,plan,development_rows):
    manifest=json.loads((directory/'manifest.json').read_bytes());rows={}
    if manifest['plan_id']!=sha(canonical(plan)) or set(manifest['tables'])!=set(health.SCHEMAS): raise ValueError('Evaluation manifest/plan mismatch')
    if sha((directory/'baselines.json').read_bytes())!=plan['baselines_sha256']: raise ValueError('Frozen baseline changed')
    baselines=json.loads((directory/'baselines.json').read_bytes())
    for name,schema in health.SCHEMAS.items():
        path=directory/(name+'.parquet');table=pq.read_table(path);rows[name]=table.to_pylist();info=manifest['tables'][name]
        if table.schema!=schema or sha(path.read_bytes())!=info['file_sha256'] or len(rows[name])!=info['rows'] or features.logical_hash(rows[name],schema)!=info['logical_sha256']: raise ValueError('Evaluation table integrity mismatch')
    keys=dict(recordings='recording_id',features='feature_id',scores='score_id',alerts='alert_id',alert_evidence='evidence_id',findings='finding_id',coverage='slot_id')
    for name,key in keys.items():
        if len(rows[name])!=len({row[key] for row in rows[name]}): raise ValueError('Duplicate evaluation identities')
    original={name:rows[name] for name in features.SCHEMAS}
    original['coverage']=[{field.name:row[field.name] for field in features.SCHEMAS['coverage']} for row in rows['coverage']]
    original['findings']=[row for row in rows['findings'] if row['scope']!='SCORING']
    rebuilt=derive(original,baselines,plan['policy_sha256'],development_rows)
    if rebuilt!=rows: raise ValueError('Evaluation score/state/lineage mismatch')
    report_bytes=(directory/'metrics.json').read_bytes()
    if sha(report_bytes)!=manifest['metrics_sha256']: raise ValueError('Metric checksum mismatch')
    snapshot=json.loads((directory/'receipt-snapshot.json').read_bytes())
    if sha(canonical(snapshot))!=plan['receipt_snapshot_sha256']: raise ValueError('Receipt snapshot mismatch')
    if metrics(rows,snapshot,plan['metric_definitions'],baselines)!=json.loads(report_bytes): raise ValueError('Metric definitions/denominators mismatch')
    return manifest


def build(engine,analytics,plan_id,*,recompute=False,after_promotion=None):
    plan_id=valid_id(plan_id);plan_bytes=(analytics/'evaluation-plans'/(plan_id+'.json')).read_bytes()
    if sha(plan_bytes)!=plan_id: raise ValueError('Frozen plan checksum mismatch')
    plan=json.loads(plan_bytes)
    with engine.connect() as connection:
        if connection.scalar(text('SELECT document FROM evaluation_plans WHERE plan_id=:id'),{'id':plan_id})!=plan: raise ValueError('Unregistered plan')
    directory,development,source,feature_manifest,baseline=load_inputs(engine,analytics,plan['development_build_id'])
    if sha(canonical(development))!=plan['development_manifest_sha256'] or sha(canonical(feature_manifest))!=plan['feature_manifest_sha256'] or sha(baseline)!=plan['baselines_sha256']: raise ValueError('Frozen input mismatch')
    if sha(Path(__file__).read_bytes())!=plan['implementation_sha256'] or features.environment_identity()!=plan['environment']: raise ValueError('Evaluation code/environment changed; freeze a new explicit plan')
    for filename,key in [('split-manifest-v1.json','split_sha256'),('evaluation-metrics-v1.json','metric_definition_sha256'),('condition-monitoring-config.json','configuration_sha256')]:
        if sha((features.CONTRACTS/filename).read_bytes())!=plan[key]: raise ValueError('Frozen configuration changed')
    development_rows=read_rows(directory);input_rows={name:pq.read_table(source/(name+'.parquet')).to_pylist() for name in features.SCHEMAS}
    snapshot_bytes=(analytics/'snapshots'/(plan['receipt_snapshot_sha256']+'.json')).read_bytes()
    if sha(snapshot_bytes)!=plan['receipt_snapshot_sha256']: raise ValueError('Input receipt snapshot changed')
    # Snapshots are canonical when created by the feature freeze.
    snapshot=json.loads(snapshot_bytes)
    destination=analytics/'evaluation-builds'/plan_id
    with (analytics/'evaluation-build.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if destination.exists() and not recompute: manifest=verify(destination,plan,development_rows)
        else:
            rows=derive(input_rows,json.loads(baseline),plan['policy_sha256'],development_rows)
            report=metrics(rows,snapshot,plan['metric_definitions'],json.loads(baseline));destination.parent.mkdir(exist_ok=True)
            (analytics/'.staging').mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='evaluation-',dir=analytics/'.staging') as tmp:
                tmp=Path(tmp);tables={}
                for name,schema in health.SCHEMAS.items():
                    path=tmp/(name+'.parquet');table=pa.Table.from_pylist(rows[name],schema=schema);pq.write_table(table,path,compression='zstd',version='2.6')
                    with path.open('rb') as stream: os.fsync(stream.fileno())
                    tables[name]=dict(rows=table.num_rows,file_sha256=sha(path.read_bytes()),logical_sha256=features.logical_hash(table.to_pylist(),schema))
                durable_write(tmp/'baselines.json',baseline);durable_write(tmp/'receipt-snapshot.json',snapshot_bytes)
                durable_write(tmp/'metrics.json',canonical(report))
                manifest=dict(schema_version='1.0.0',stage='CHRONOLOGICAL_EVALUATION',build_id=plan_id,plan_id=plan_id,
                    development_build_id=plan['development_build_id'],feature_build_id=plan['feature_build_id'],tables=tables,
                    baselines_sha256=plan['baselines_sha256'],policy_sha256=plan['policy_sha256'],metrics_sha256=sha(canonical(report)))
                durable_write(tmp/'manifest.json',canonical(manifest));verify(tmp,plan,development_rows)
                if destination.exists():
                    if verify(destination,plan,development_rows)!=manifest: raise ValueError('Evaluation recomputation differs')
                else:
                    os.rename(tmp,destination);parent=os.open(destination.parent,os.O_DIRECTORY)
                    try: os.fsync(parent)
                    finally: os.close(parent)
        if after_promotion: after_promotion(manifest)
        with engine.begin() as connection:
            connection.execute(text('''INSERT INTO evaluation_builds(build_id,plan_id,manifest_sha256,manifest,metrics)
                VALUES(:id,:plan,:sha,CAST(:manifest AS jsonb),CAST(:metrics AS jsonb)) ON CONFLICT(build_id) DO NOTHING'''),
                dict(id=manifest['build_id'],plan=plan_id,sha=sha(canonical(manifest)),manifest=canonical(manifest).decode(),metrics=(destination/'metrics.json').read_text()))
            if connection.scalar(text('SELECT manifest_sha256 FROM evaluation_builds WHERE build_id=:id'),{'id':plan_id})!=sha(canonical(manifest)): raise ValueError('Evaluation registry mismatch')
        return manifest,json.loads((destination/'metrics.json').read_bytes())


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='operation',required=True)
    freeze_parser=sub.add_parser('freeze');freeze_parser.add_argument('--development-build',required=True)
    build_parser=sub.add_parser('build');build_parser.add_argument('--plan',required=True);build_parser.add_argument('--recompute',action='store_true')
    args=parser.parse_args()
    from component_health.database import build_engine
    from component_health.settings import Settings
    settings=Settings();engine=build_engine(settings);started=time.perf_counter()
    try:
        if args.operation=='freeze': result=dict(plan_id=freeze(engine,settings.analytical_directory,args.development_build),status='FROZEN_BEFORE_SCORING')
        else:
            manifest,report=build(engine,settings.analytical_directory,args.plan,recompute=args.recompute)
            result=dict(build_id=manifest['build_id'],operational=report['operational'],detector_suitability=report['detector_suitability'])
        result.update(elapsed_seconds=round(time.perf_counter()-started,3),worker_peak_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,3))
        print(json.dumps(result,sort_keys=True))
    except Exception: parser.exit(1,'Evaluation failed; frozen inputs, policy and output were not overwritten. Check integrity/version requirements.\n')
    finally: engine.dispose()


if __name__=='__main__':main()
