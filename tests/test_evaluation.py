import json,runpy
from copy import deepcopy
import pytest
from component_health import health,evaluation,features


def fixture():
    factory=runpy.run_path('tests/test_detection.py')['fixture_rows']
    rows=factory(development=(100.,100.,1.,1.,1.,100.,100.),evaluation=(100.,1.,100.,100.,1.,1.,1.))
    development,baselines=health.derive(rows)
    return rows,development,baselines


def test_frozen_parameter_use_partition_reset_and_denominators(monkeypatch):
    rows,development,baselines=fixture();before=deepcopy(baselines)
    def reject_fit(*args,**kwargs): raise AssertionError('Evaluation must not fit')
    monkeypatch.setattr(health,'fit_baselines',reject_fit)
    final=evaluation.derive(rows,baselines,baselines[0]['policy_sha256'],development)
    assert baselines==before
    assert [row for row in final['scores'] if row['partition']=='development']==development['scores']
    first=next(row for row in final['coverage'] if row['partition']=='evaluation')
    assert first['entry_streak']==1 and first['active_alert_id'] is None
    assert len([row for row in final['alerts'] if row['partition']=='evaluation'])==4
    definitions=json.loads((features.CONTRACTS/'evaluation-metrics-v1.json').read_bytes())
    report=evaluation.metrics(final,{'observations':{}},definitions,baselines)
    metrics=report['operational']['evaluation']['all']
    assert metrics['score_coverage']==evaluation.ratio(28,28)
    assert metrics['threshold_burden']==evaluation.ratio(12,28)
    assert metrics['alerts']==4 and metrics['closed']==4 and metrics['open_at_boundary']==0
    assert all(row['value'] is None and row['status']=='NOT_APPLICABLE' for row in report['unsupported'].values())
    assert report['duplicate_delivery_attempts']['status']=='NOT_MEASURED'


def test_unscorable_denominator_and_zero_valid_ratio():
    rows,development,baselines=fixture()
    for slot in rows['coverage']:
        if slot['partition']=='evaluation':slot['feature_eligible']=False;slot['reason_codes']=['MISSING_SOURCE'];slot['feature_id']=None
    final=evaluation.derive(rows,baselines,baselines[0]['policy_sha256'],development)
    definitions=json.loads((features.CONTRACTS/'evaluation-metrics-v1.json').read_bytes())
    report=evaluation.metrics(final,{'observations':{}},definitions,baselines)['operational']['evaluation']['all']
    assert report['score_rows']==28 and report['unscorable_slots']==28
    assert report['score_coverage']==evaluation.ratio(0,28)
    assert report['threshold_burden']==evaluation.ratio(0,0)
    assert report['threshold_burden']['value'] is None
    assert report['exclusion_reasons']=={'MISSING_SOURCE':28}


def test_development_tamper_is_not_silently_accepted():
    rows,development,baselines=fixture();development['scores'][0]['value']=999.
    with pytest.raises(ValueError,match='Development result changed'):evaluation.derive(rows,baselines,baselines[0]['policy_sha256'],development)
