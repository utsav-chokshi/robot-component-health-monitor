"""Reference-only RMS/MAD calibration and development-only health publication."""
import argparse
from collections import Counter
from datetime import datetime
import fcntl
import inspect
import json
import math
import os
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import tempfile
import time

from jsonschema import Draft202012Validator, FormatChecker
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text
from component_health import features
from component_health.ingestion import canonical, sha, identity, durable_write, RELEASE

POLICY=dict(version='rms-mad-alerts-v1',minimum_reference=32,mad_factor=1.4826,entry=6.0,clear=4.0,
            entry_count=2,clear_count=3,interval_seconds=600,reference_health='ASSUMED_NOT_LABELED')


def policy_digest():
    return sha(canonical({'policy':POLICY,'algorithm_sha256':sha(Path(__file__).read_bytes())}))


def fit_baselines(coverage, feature_rows,policy_sha=None):
    by_id={row['feature_id']:row for row in feature_rows}; output=[]
    for channel in range(1,5):
        used=[];excluded=[]
        for slot in sorted(coverage,key=lambda row:row['ordinal']):
            if slot['partition']!='reference' or slot['channel_index']!=channel: continue
            feature=by_id.get(slot['feature_id'])
            if slot['feature_eligible'] and feature and feature['quality_status']=='VALID' and feature['rms'] is not None and math.isfinite(feature['rms']) and feature['rms']>0:
                used.append(dict(slot_id=slot['slot_id'],feature_id=feature['feature_id'],rms=feature['rms']))
            else:
                excluded.append(dict(slot_id=slot['slot_id'],reason_codes=slot['reason_codes'] or ['INVALID_REFERENCE_FEATURE']))
        reason=None;location=None;mad=None;scale=None
        if len(used)<POLICY['minimum_reference']: reason='BASELINE_INSUFFICIENT_REFERENCE'
        else:
            values=[row['rms'] for row in used]
            location=float(statistics.median(values));mad=float(statistics.median(abs(value-location) for value in values))
            scale=POLICY['mad_factor']*mad
            if not all(math.isfinite(value) for value in [location,mad,scale]) or scale<0: reason='BASELINE_INVALID'
            elif scale==0: reason='BASELINE_NO_VARIATION'
        parameters={key:value if value is None or math.isfinite(value) else None for key,value in dict(location=location,mad=mad,scale=scale).items()}
        baseline=dict(channel_index=channel,method='median-rms-mad',policy_sha256=policy_sha or policy_digest(),reference_health='ASSUMED_NOT_LABELED',amplitude_unit='native_numeric',physical_unit_verified=False,
                      used=used,excluded=excluded,declared_reference_slots=len(used)+len(excluded),
                      status='UNUSABLE' if reason else 'VALID',reason_codes=[reason] if reason else [],**parameters)
        output.append(dict(baseline_id=identity(baseline),**baseline))
    return output


def score_slots(coverage, feature_rows, baselines, partitions=('development',),policy_sha=None):
    by_id={row['feature_id']:row for row in feature_rows}; by_channel={row['channel_index']:row for row in baselines}
    scores=[];policy=policy_sha or policy_digest()
    for slot in sorted(coverage,key=lambda row:(row['ordinal'],row['channel_index'])):
        if slot['partition'] not in partitions: continue
        baseline=by_channel[slot['channel_index']];feature=by_id.get(slot['feature_id']);reasons=[];value=None;direction=None
        if not slot['feature_eligible'] or feature is None or feature['quality_status']!='VALID': reasons.extend(slot['reason_codes'] or ['FEATURE_UNAVAILABLE'])
        if baseline['status']!='VALID': reasons.extend(baseline['reason_codes'])
        if not reasons:
            rms=feature['rms']
            if rms is None or not math.isfinite(rms) or rms<=0: reasons.append('INVALID_FEATURE_VALUE')
            else:
                value=abs(rms-baseline['location'])/baseline['scale']
                if not math.isfinite(value): value=None;reasons.append('NONFINITE_SCORE')
                else: direction='ABOVE_REFERENCE' if rms>baseline['location'] else 'BELOW_REFERENCE' if rms<baseline['location'] else 'AT_REFERENCE'
        scores.append(dict(schema_version='1.0.0',score_id=identity(dict(window_id=slot['slot_id'],baseline_id=baseline['baseline_id'],policy_sha256=policy)),
            window_id=slot['slot_id'],feature_id=slot['feature_id'],baseline_id=baseline['baseline_id'],channel_index=slot['channel_index'],partition=slot['partition'],
            source_time=slot['expected_source_time'],source_timezone='UNSPECIFIED',quantity_kind='robust_deviation',unit='dimensionless',
            value=value,direction=direction,entry_threshold=6.0,clear_threshold=4.0,status='UNSCORABLE' if reasons else 'SCORABLE',reason_codes=sorted(set(reasons))))
    return scores


def alert_lifecycle(scores, coverage,policy_sha=None):
    slots={row['slot_id']:row for row in coverage};alerts=[];evidence=[];states={}
    for partition,channel in sorted({(row['partition'],row['channel_index']) for row in scores}):
        rows=sorted([row for row in scores if (row['partition'],row['channel_index'])==(partition,channel)],key=lambda row:slots[row['window_id']]['ordinal'])
        pending=[];clear=0;active=None;previous=None
        def link(alert,score,role):
            item=dict(alert_id=alert['alert_id'],score_id=score['score_id'],window_id=score['window_id'],role=role)
            evidence.append(dict(evidence_id=identity(item),**item))
        for score in rows:
            slot=slots[score['window_id']];now=datetime.fromisoformat(score['source_time'])
            gap=previous is not None and (slot['ordinal']!=previous[0]+1 or (now-previous[1]).total_seconds()!=600)
            valid=score['status']=='SCORABLE'
            if gap or not valid:
                pending=[];clear=0
                if active: active['coverage_interrupted']=True
            if gap and active: link(active,score,'CAPTURE_GAP')
            if active: active['latest_processed_time']=score['source_time']
            current_alert=active['alert_id'] if active else None
            transition='NONE'
            if not valid:
                if active: link(active,score,'UNSCORABLE_GAP')
            elif active:
                if score['value']<4:
                    clear+=1;link(active,score,'CLEARANCE' if clear==3 else 'CLEAR_CANDIDATE')
                    if clear==3:
                        active['closed_time']=score['source_time'];active['state']='CLOSED';transition='CLOSED';active=None;clear=0
                else:
                    clear=0;link(active,score,'PERSISTENCE')
            else:
                if score['value']>=6:
                    pending.append(score)
                    if len(pending)==2:
                        first=pending[0]
                        active=dict(alert_id=identity(dict(partition=partition,channel_index=channel,policy_sha256=policy_sha or policy_digest(),first_score_id=first['score_id'])),
                            channel_index=channel,partition=partition,source_timezone='UNSPECIFIED',policy_sha256=policy_sha or policy_digest(),state='OPEN_AT_BOUNDARY',
                            start_trigger_score_id=first['score_id'],activation_score_id=score['score_id'],first_high_time=first['source_time'],
                            activation_time=score['source_time'],latest_processed_time=score['source_time'],closed_time=None,coverage_interrupted=False)
                        alerts.append(active);link(active,first,'ENTRY_FIRST');link(active,score,'ACTIVATION');pending=[]
                        current_alert=active['alert_id'];transition='ACTIVATED'
                else: pending=[]
            states[score['window_id']]=dict(score_id=score['score_id'],score_status=score['status'],score_reason_codes=score['reason_codes'],
                active_alert_id=active['alert_id'] if active else None,
                observed_state='UNAVAILABLE' if not valid else 'ACTIVE_ALERT' if active else 'ALERT_CLOSED' if transition=='CLOSED' else 'NO_ACTIVE_ALERT_WITH_VALID_SCORE',
                entry_streak=len(pending),clear_streak=clear,coverage_uncertain=not valid or gap or bool(active and active['coverage_interrupted']),transition=transition)
            previous=(slot['ordinal'],now)
    return alerts,evidence,states


S=pa.string();I=pa.int32();B=pa.bool_();F=pa.float64();L=pa.list_(pa.field('element',S))
SCHEMAS={**features.SCHEMAS,
 'scores':pa.schema([('schema_version',S),('score_id',S),('window_id',S),('feature_id',S),('baseline_id',S),('channel_index',I),('partition',S),
     ('source_time',S),('source_timezone',S),('quantity_kind',S),('unit',S),('value',F),('direction',S),('entry_threshold',F),('clear_threshold',F),('status',S),('reason_codes',L)]),
 'alerts':pa.schema([('alert_id',S),('channel_index',I),('partition',S),('source_timezone',S),('policy_sha256',S),('state',S),('start_trigger_score_id',S),('activation_score_id',S),
     ('first_high_time',S),('activation_time',S),('latest_processed_time',S),('closed_time',S),('coverage_interrupted',B)]),
 'alert_evidence':pa.schema([('evidence_id',S),('alert_id',S),('score_id',S),('window_id',S),('role',S)])}
SCHEMAS['coverage']=pa.schema(list(features.SCHEMAS['coverage'])+[pa.field(name,kind) for name,kind in [
    ('score_id',S),('score_status',S),('score_reason_codes',L),('active_alert_id',S),('observed_state',S),('entry_streak',I),('clear_streak',I),('coverage_uncertain',B),('transition',S)]])


def derive(input_rows,partitions=('development',)):
    if partitions!=('development',): raise ValueError('Real evaluation scoring is reserved for Step 9')
    baselines=fit_baselines(input_rows['coverage'],input_rows['features'])
    scores=score_slots(input_rows['coverage'],input_rows['features'],baselines,partitions)
    validator=Draft202012Validator(json.loads((features.CONTRACTS/'schemas/score-row.schema.json').read_bytes()),format_checker=FormatChecker())
    for score in scores: validator.validate(score)
    alerts,evidence,states=alert_lifecycle(scores,input_rows['coverage'])
    coverage=[]
    for row in input_rows['coverage']:
        if row['partition'] in partitions: state=states[row['slot_id']]
        else:
            ref=row['partition']=='reference'
            state=dict(score_id=None,score_status='NOT_APPLICABLE' if ref else 'NOT_EVALUATED',score_reason_codes=['REFERENCE_FIT_ONLY' if ref else 'EVALUATION_RESERVED_FOR_STEP_9'],
                active_alert_id=None,observed_state='REFERENCE_FIT_ONLY' if ref else 'NOT_EVALUATED',entry_streak=0,clear_streak=0,coverage_uncertain=not row['feature_eligible'],transition='NONE')
        coverage.append(dict(**row,**state))
    findings=list(input_rows['findings']);slot_map={row['slot_id']:row for row in coverage}
    for score in scores:
        slot=slot_map[score['window_id']]
        for reason in score['reason_codes']:
            finding=dict(recording_id=slot['recording_id'],channel_index=slot['channel_index'],receipt_id=None,reason_code=reason,scope='SCORING')
            findings.append(dict(finding_id=identity(finding),**finding))
    result={**input_rows,'findings':sorted(findings,key=lambda row:row['finding_id']),'coverage':coverage,'scores':scores,'alerts':sorted(alerts,key=lambda row:row['alert_id']),
            'alert_evidence':sorted(evidence,key=lambda row:row['evidence_id'])}
    return result,baselines


def validate_rows(rows,baselines):
    features_by_id={row['feature_id']:row for row in rows['features']};slots={row['slot_id']:row for row in rows['coverage']}
    scores={row['score_id']:row for row in rows['scores']};alerts={row['alert_id']:row for row in rows['alerts']};base={row['baseline_id']:row for row in baselines}
    if len(baselines)!=4 or len({row['channel_index'] for row in baselines})!=4: raise ValueError('Baseline channel mismatch')
    policy=baselines[0]['policy_sha256']
    if any(row['policy_sha256']!=policy for row in baselines) or fit_baselines(rows['coverage'],rows['features'],policy)!=baselines: raise ValueError('Baseline reference fit mismatch')
    if score_slots(rows['coverage'],rows['features'],baselines,policy_sha=policy)!=rows['scores']: raise ValueError('Score calculation mismatch')
    for name,key in [('recordings','recording_id'),('features','feature_id'),('coverage','slot_id'),('scores','score_id'),('alerts','alert_id'),('alert_evidence','evidence_id'),('findings','finding_id')]:
        if len(rows[name])!=len({row[key] for row in rows[name]}): raise ValueError('Duplicate health identities')
    if len(slots)!=len(rows['recordings'])*4: raise ValueError('Health denominator mismatch')
    for score in scores.values():
        slot=slots[score['window_id']]
        if slot['partition']!='development' or (slot['channel_index'],slot['partition'])!=(score['channel_index'],score['partition']): raise ValueError('Score partition/slot mismatch')
        if score['baseline_id'] not in base or base[score['baseline_id']]['channel_index']!=score['channel_index']: raise ValueError('Baseline lineage mismatch')
        if score['feature_id'] is not None and score['feature_id'] not in features_by_id: raise ValueError('Score feature missing')
        if score['status']=='SCORABLE' and (score['value'] is None or not math.isfinite(score['value']) or score['value']<0 or not slot['feature_eligible']): raise ValueError('Invalid scorable value')
    if len(scores)!=sum(row['partition']=='development' for row in slots.values()): raise ValueError('Scoring denominator mismatch')
    for evidence in rows['alert_evidence']:
        if evidence['alert_id'] not in alerts or evidence['score_id'] not in scores or scores[evidence['score_id']]['window_id']!=evidence['window_id']: raise ValueError('Alert evidence lineage mismatch')
        alert=alerts[evidence['alert_id']];score=scores[evidence['score_id']]
        if (alert['channel_index'],alert['partition'])!=(score['channel_index'],score['partition']): raise ValueError('Cross-component evidence')
    # Recompute lifecycle independently from persisted states, preventing inconsistent bundles.
    expected_alerts,expected_evidence,states=alert_lifecycle(list(scores.values()),list(slots.values()),policy)
    if sorted(expected_alerts,key=lambda row:row['alert_id'])!=rows['alerts'] or sorted(expected_evidence,key=lambda row:row['evidence_id'])!=rows['alert_evidence']: raise ValueError('Alert lifecycle mismatch')
    for slot in slots.values():
        if slot['partition']=='development' and any(slot[key]!=value for key,value in states[slot['slot_id']].items()): raise ValueError('Coverage state mismatch')
    return dict(recordings=len(rows['recordings']),expected_channel_slots=len(slots),score_slots=len(scores),
        scorable=sum(row['status']=='SCORABLE' for row in scores.values()),unscorable=sum(row['status']=='UNSCORABLE' for row in scores.values()),
        evaluation_scored=0,evaluation_reserved_slots=sum(row['partition']=='evaluation' for row in slots.values()),
        baselines_valid=sum(row['status']=='VALID' for row in baselines),alerts=len(alerts),closed_alerts=sum(row['state']=='CLOSED' for row in alerts.values()),
        open_boundary_alerts=sum(row['state']=='OPEN_AT_BOUNDARY' for row in alerts.values()),interrupted_alerts=sum(row['coverage_interrupted'] for row in alerts.values()),
        threshold_exceedance_windows=sum(row['status']=='SCORABLE' and row['value']>=6 for row in scores.values()),
        pending_entry_groups=sum(row['entry_streak']>0 for row in slots.values() if row['partition']=='development' and row['ordinal']==max(s['ordinal'] for s in slots.values() if s['partition']=='development')))


def verify_health(directory):
    manifest=json.loads((directory/'manifest.json').read_bytes());rows={}
    if set(manifest['tables'])!=set(SCHEMAS): raise ValueError('Incomplete health tables')
    baseline_bytes=(directory/'baselines.json').read_bytes()
    if sha(baseline_bytes)!=manifest['baselines_sha256']: raise ValueError('Baseline checksum mismatch')
    baselines=json.loads(baseline_bytes)
    for name,schema in SCHEMAS.items():
        path=directory/(name+'.parquet');info=manifest['tables'][name];table=pq.read_table(path);rows[name]=table.to_pylist()
        if table.schema!=schema or sha(path.read_bytes())!=info['file_sha256'] or len(rows[name])!=info['rows'] or features.logical_hash(rows[name],schema)!=info['logical_sha256']: raise ValueError('Health table integrity mismatch')
    if validate_rows(rows,baselines)!=manifest['metrics']: raise ValueError('Health metrics mismatch')
    return manifest


def register(engine,manifest):
    digest=sha(canonical(manifest))
    with engine.begin() as connection:
        connection.execute(text('''INSERT INTO health_builds(build_id,feature_build_id,manifest_sha256,manifest)
            VALUES(:id,:feature,:sha,CAST(:manifest AS jsonb)) ON CONFLICT(build_id) DO NOTHING'''),
            dict(id=manifest['build_id'],feature=manifest['feature_build_id'],sha=digest,manifest=canonical(manifest).decode()))
        if connection.scalar(text('SELECT manifest_sha256 FROM health_builds WHERE build_id=:id'),{'id':manifest['build_id']})!=digest: raise ValueError('Health registry mismatch')


def build(engine,analytics,feature_build_id,*,recompute=False,after_promotion=None):
    if len(feature_build_id)!=64 or any(c not in '0123456789abcdef' for c in feature_build_id): raise ValueError('Invalid feature ID')
    source=analytics/'feature-builds'/feature_build_id;feature_manifest=features.verify_bundle(source)
    with engine.connect() as connection:
        registered=connection.scalar(text('SELECT manifest_sha256 FROM feature_builds WHERE build_id=:id'),{'id':feature_build_id})
    if registered!=sha(canonical(feature_manifest)) or feature_manifest['build_id']!=feature_build_id: raise ValueError('Unregistered/mismatched feature input')
    config_bytes=(features.CONTRACTS/'condition-monitoring-config.json').read_bytes();config=json.loads(config_bytes)
    if config['baseline']!=dict(fit_partition='reference',minimum_valid_windows_per_channel=32,location='median_rms',scale='1.4826_times_median_absolute_deviation',score='abs(rms-location)/scale',invalid_scale='UNSCORABLE',reference_health='ASSUMED_NOT_LABELED') or config['alerts']!=dict(entry_score_gte=6.0,entry_consecutive_valid_slots=2,clear_score_lt=4.0,clear_consecutive_valid_slots=3,expected_capture_interval_seconds=600,gap_or_unscorable='RESET_STREAKS_KEEP_ACTIVE_MARK_UNKNOWN',partition_state='RESET_AT_EACH_PARTITION',end_active_state='OPEN_AT_BOUNDARY'):
        raise ValueError('Unsupported monitoring configuration')
    provenance=dict(feature_manifest_sha256=registered,policy_sha256=policy_digest(),configuration_sha256=sha(config_bytes),
        score_schema_sha256=sha((features.CONTRACTS/'schemas/score-row.schema.json').read_bytes()),environment=features.environment_identity(),partitions=['development'])
    build_id=sha(canonical(provenance));destination=analytics/'health-builds'/build_id
    with (analytics/'health-build.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if destination.exists() and not recompute:
            manifest=verify_health(destination)
            if manifest['build_id']!=build_id or manifest['provenance']!=provenance: raise ValueError('Health identity mismatch')
            register(engine,manifest);return manifest
        input_rows={name:pq.read_table(source/(name+'.parquet')).to_pylist() for name in features.SCHEMAS}
        rows,baselines=derive(input_rows);metrics=validate_rows(rows,baselines)
        (analytics/'.staging').mkdir(exist_ok=True);destination.parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='health-',dir=analytics/'.staging') as tmp:
            tmp=Path(tmp);tables={}
            for name,schema in SCHEMAS.items():
                path=tmp/(name+'.parquet');table=pa.Table.from_pylist(rows[name],schema=schema)
                pq.write_table(table,path,compression='zstd',version='2.6')
                with path.open('rb') as stream: os.fsync(stream.fileno())
                tables[name]=dict(rows=table.num_rows,file_sha256=sha(path.read_bytes()),logical_sha256=features.logical_hash(table.to_pylist(),schema))
            baseline_bytes=canonical(baselines);durable_write(tmp/'baselines.json',baseline_bytes)
            manifest=dict(schema_version='1.0.0',stage='DEVELOPMENT_HEALTH',build_id=build_id,feature_build_id=feature_build_id,
                dataset_release=RELEASE,provenance=provenance,policy=POLICY,baselines_sha256=sha(baseline_bytes),tables=tables,metrics=metrics)
            durable_write(tmp/'manifest.json',canonical(manifest));verify_health(tmp)
            if destination.exists():
                if verify_health(destination)!=manifest: raise ValueError('Health recomputation differs')
            else:
                os.rename(tmp,destination);parent=os.open(destination.parent,os.O_DIRECTORY)
                try: os.fsync(parent)
                finally: os.close(parent)
        if after_promotion: after_promotion(manifest)
        register(engine,manifest);return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--feature-build',required=True);parser.add_argument('--recompute',action='store_true')
    args=parser.parse_args()
    from component_health.database import build_engine
    from component_health.settings import Settings
    settings=Settings();engine=build_engine(settings);started=time.perf_counter()
    try:
        manifest=build(engine,settings.analytical_directory,args.feature_build,recompute=args.recompute)
        print(json.dumps(dict(build_id=manifest['build_id'],metrics=manifest['metrics'],elapsed_seconds=round(time.perf_counter()-started,3),
                             worker_peak_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,3)),sort_keys=True))
    except Exception: parser.exit(1,'Development health build failed; check frozen feature integrity and configuration.\n')
    finally: engine.dispose()


if __name__=='__main__': main()
