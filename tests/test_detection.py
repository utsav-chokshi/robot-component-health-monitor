from copy import deepcopy
from datetime import datetime,timedelta
import math
import pytest
from component_health.health import fit_baselines,score_slots,alert_lifecycle,derive
from component_health.ingestion import identity,sha


def fixture_rows(reference=32,development=(1.,1.,1.),evaluation=(1000.,)):
    result=dict(recordings=[],features=[],coverage=[],findings=[])
    values=[float(index+1) for index in range(reference)]+list(development)+list(evaluation)
    for ordinal,rms in enumerate(values,1):
        partition='reference' if ordinal<=reference else 'development' if ordinal<=reference+len(development) else 'evaluation'
        timestamp=(datetime(2004,2,12,10,32,39)+timedelta(seconds=600*(ordinal-1))).isoformat()
        rid=identity({'recording':ordinal});digest=sha(str(ordinal).encode())
        result['recordings'].append(dict(recording_id=rid,source_key='2nd_test/'+timestamp.replace('-','.').replace('T','.').replace(':','.'),source_sha256=digest,
            accepted_receipt_id=identity({'receipt':ordinal}),source_time=timestamp,time_basis='OBSERVED_FILENAME',source_timezone='UNSPECIFIED',ordinal=ordinal,
            partition=partition,amplitude_unit='native_numeric',physical_unit_verified=False,timing_uncertainty=True))
        for channel in range(1,5):
            fid=identity({'feature':ordinal,'channel':channel});slot=identity({'slot':ordinal,'channel':channel})
            result['features'].append(dict(schema_version='1.0.0',feature_id=fid,recording_id=rid,channel_index=channel,source_sha256=digest,
                ordinal=ordinal,partition=partition,sample_start=0,sample_stop_exclusive=20480,feature_definition='time-domain-v1',amplitude_unit='native_numeric',
                crest_unit='dimensionless_ratio',rms=rms,std_population=1.,absolute_peak=2*rms,crest_factor=2.,quality_status='VALID',reason_codes=[]))
            result['coverage'].append(dict(slot_id=slot,recording_id=rid,component_id=identity({'component':channel}),channel_index=channel,ordinal=ordinal,
                partition=partition,expected_source_time=timestamp,source_timezone='UNSPECIFIED',source_present=True,source_admission='ACCEPTED',feature_id=fid,
                feature_eligible=True,reason_codes=[]))
    return result


def test_reference_only_oracle_direction_and_future_invariance():
    rows=fixture_rows(development=(16.5,40.,1.))
    base=fit_baselines(rows['coverage'],rows['features'])
    assert all(row['location']==16.5 and row['mad']==8 and row['scale']==pytest.approx(11.8608) for row in base)
    scores=score_slots(rows['coverage'],rows['features'],base)
    first=[row for row in scores if row['channel_index']==1]
    assert [row['direction'] for row in first]==['AT_REFERENCE','ABOVE_REFERENCE','BELOW_REFERENCE']
    assert first[0]['value']==0 and first[1]['value']==pytest.approx(23.5/11.8608)
    future=deepcopy(rows)
    for feature in future['features']:
        if feature['partition']=='evaluation': feature['rms']=1e200
    assert fit_baselines(future['coverage'],future['features'])==base
    assert score_slots(future['coverage'],future['features'],base)==scores
    output,_=derive(rows)
    assert all(slot['score_status']=='NOT_EVALUATED' and slot['score_id'] is None for slot in output['coverage'] if slot['partition']=='evaluation')
    with pytest.raises(ValueError): derive(rows,('evaluation',))


@pytest.mark.parametrize('kind,reason',[('insufficient','BASELINE_INSUFFICIENT_REFERENCE'),('zero_mad','BASELINE_NO_VARIATION'),('overflow','BASELINE_INVALID')])
def test_unusable_baseline_abstains(kind,reason):
    rows=fixture_rows(reference=31 if kind=='insufficient' else 32)
    if kind!='insufficient':
        for row in rows['features']:
            if row['partition']=='reference': row['rms']=2. if kind=='zero_mad' else 1e308
    baselines=fit_baselines(rows['coverage'],rows['features'])
    assert all(row['reason_codes']==[reason] for row in baselines)
    scores=score_slots(rows['coverage'],rows['features'],baselines)
    assert all(row['value'] is None and row['status']=='UNSCORABLE' and reason in row['reason_codes'] for row in scores)


def score_sequence(values,partition='development',offset=0,time_jump_at=None):
    coverage=[];scores=[]
    for index,value in enumerate(values):
        timestamp=(datetime(2004,2,13)+timedelta(seconds=600*(index+offset)+(600 if time_jump_at is not None and index>=time_jump_at else 0))).isoformat()
        window=identity(dict(window=index+offset,partition=partition));score=identity(dict(score=index+offset,partition=partition))
        coverage.append(dict(slot_id=window,ordinal=index+offset+1))
        scores.append(dict(score_id=score,window_id=window,channel_index=1,partition=partition,source_time=timestamp,
                           status='UNSCORABLE' if value is None else 'SCORABLE',value=value,reason_codes=['MISSING_SOURCE'] if value is None else []))
    return scores,coverage


@pytest.mark.parametrize('values,expected', [([6,6],['OPEN_AT_BOUNDARY']),([6,5,6],[]),([6,6,4,3,3,3],['CLOSED']),
    ([6,6,3,4,3,3],['OPEN_AT_BOUNDARY']),([6,6,3,3,3,6,6],['CLOSED','OPEN_AT_BOUNDARY']),([6],[])])
def test_entry_clear_boundaries_and_multiple_episodes(values,expected):
    scores,slots=score_sequence(values);alerts,evidence,states=alert_lifecycle(scores,slots)
    assert [row['state'] for row in alerts]==expected
    if alerts:
        assert alerts[0]['first_high_time']==scores[0]['source_time'] and alerts[0]['activation_time']==scores[1]['source_time']
        assert alerts[0]['start_trigger_score_id']==scores[0]['score_id']
        assert {row['score_id'] for row in evidence if row['role'] in {'ENTRY_FIRST','ACTIVATION'}}.issuperset({scores[0]['score_id'],scores[1]['score_id']})
    assert alert_lifecycle(list(reversed(scores)),slots)==(alerts,evidence,states)


def test_gaps_do_not_bridge_streaks_or_clear_active_alerts():
    scores,slots=score_sequence([6,None,6,6,None,3,3,3]);alerts,evidence,states=alert_lifecycle(scores,slots)
    assert len(alerts)==1 and alerts[0]['state']=='CLOSED' and alerts[0]['coverage_interrupted']
    assert alerts[0]['activation_time']==scores[3]['source_time']
    assert states[scores[4]['window_id']]['observed_state']=='UNAVAILABLE'
    assert states[scores[-1]['window_id']]['active_alert_id'] is None
    assert any(row['role']=='UNSCORABLE_GAP' for row in evidence)
    scores,slots=score_sequence([6,6],time_jump_at=1)
    assert alert_lifecycle(scores,slots)[0]==[]
    scores,slots=score_sequence([6,6,3,3,3],time_jump_at=3)
    alerts,evidence,_=alert_lifecycle(scores,slots)
    assert alerts[0]['state']=='OPEN_AT_BOUNDARY' and alerts[0]['coverage_interrupted']
    assert any(row['role']=='CAPTURE_GAP' for row in evidence)


def test_partition_state_reset_and_causal_prefix():
    dev,dev_slots=score_sequence([6,6]);evaluation,eval_slots=score_sequence([6,3],partition='evaluation',offset=2)
    alerts,_,states=alert_lifecycle(dev+evaluation,dev_slots+eval_slots)
    assert len(alerts)==1 and alerts[0]['partition']=='development'
    assert states[evaluation[0]['window_id']]['entry_streak']==1 and states[evaluation[0]['window_id']]['active_alert_id'] is None
    longer,longer_slots=score_sequence([6,6,3,3,3])
    _,_,long_states=alert_lifecycle(longer,longer_slots)
    assert all(states[key]==long_states[key] for key in [row['window_id'] for row in dev])


def test_missing_feature_and_nonfinite_scores_never_become_zero():
    rows=fixture_rows(development=(1e308,))
    baselines=fit_baselines(rows['coverage'],rows['features'])
    baselines[0]['scale']=1e-300
    scores=score_slots(rows['coverage'],rows['features'],baselines)
    assert scores[0]['value'] is None and scores[0]['reason_codes']==['NONFINITE_SCORE']
    for slot in rows['coverage']:
        if slot['partition']=='development': slot['feature_eligible']=False;slot['feature_id']=None;slot['reason_codes']=['MISSING_SOURCE']
    scores=score_slots(rows['coverage'],rows['features'],baselines)
    assert all(row['value'] is None and row['feature_id'] is None and row['reason_codes']==['MISSING_SOURCE'] for row in scores)


def test_reference_exclusions_remain_in_the_declared_denominator():
    rows=fixture_rows();slot=rows['coverage'][0]
    slot['feature_eligible']=False;slot['reason_codes']=['ZERO_VARIATION_CHANNEL']
    feature=next(row for row in rows['features'] if row['feature_id']==slot['feature_id'])
    feature['quality_status']='UNSCORABLE';feature['reason_codes']=['ZERO_VARIATION_CHANNEL']
    baseline=fit_baselines(rows['coverage'],rows['features'])
    assert baseline[0]['declared_reference_slots']==32 and len(baseline[0]['used'])==31 and len(baseline[0]['excluded'])==1
    assert baseline[0]['excluded'][0]['reason_codes']==['ZERO_VARIATION_CHANNEL']
    assert baseline[0]['reason_codes']==['BASELINE_INSUFFICIENT_REFERENCE']
    assert all(row['status']=='VALID' for row in baseline[1:])
