from copy import deepcopy
from pathlib import Path
from streamlit.testing.v1 import AppTest
import client

BUILD='a'*64


def api_fixture(mode='ready'):
    calls=[]
    def get(path,**params):
        calls.append((path,params))
        if mode=='unavailable':raise client.EvidenceError('The evidence service is unavailable. Retry after checking services.')
        if path=='/api/v1/evaluations':return {'items':[] if mode=='empty' else [{'build_id':BUILD}]}
        if mode=='corrupt':raise client.EvidenceError('Evidence verification failed. No verified view is available.')
        channel=params.get('channel',1)
        if path.endswith('/summary'):
            return dict(baselines=[dict(channel_index=c,location=.1,mad=.006,scale=.01,status='VALID',used_reference_slots=196,declared_reference_slots=196,amplitude_unit='native_numeric',reference_health='ASSUMED_NOT_LABELED') for c in range(1,5)],
                metrics={'unsupported':{'precision':{'status':'NOT_APPLICABLE','value':None}}},plan={'policy_sha256':'b'*64})
        rows=[]
        for ordinal,partition,rms,score in [(1,'reference',.1,None),(197,'development',.11,1.),(393,'evaluation',.165,6.5),(394,'evaluation',.17,7.)]:
            missing=mode=='missing' and ordinal==394
            rows.append(dict(ordinal=ordinal,partition=partition,recording_id=f'record-{ordinal}',expected_source_time=f'2004-02-12T10:{ordinal%60:02}:39',
                feature_eligible=not missing,source_admission='UNAVAILABLE' if missing else 'ACCEPTED',source_present=not missing,
                score_status='UNSCORABLE' if missing else 'NOT_APPLICABLE' if score is None else 'SCORABLE',score_value=None if missing else score,
                rms=None if missing else rms,std_population=.05,absolute_peak=.25,crest_factor=2.,observed_state='UNAVAILABLE' if missing else 'ACTIVE_ALERT',
                coverage_uncertain=missing,reason_codes=['MISSING_SOURCE'] if missing else [],score_reason_codes=['MISSING_SOURCE'] if missing else []))
        selected=[row for row in rows if (params.get('partition','all')=='all' or row['partition']==params['partition']) and params.get('start',1)<=row['ordinal']<=params.get('stop',984)]
        if path.endswith('/series'):return dict(items=selected,total=len(selected))
        alert=dict(alert_id='alert-1',activation_time='2004-02-12T10:34:39',first_high_time='2004-02-12T10:33:39',latest_processed_time='2004-02-12T10:34:39',closed_time=None,coverage_interrupted=False,state='OPEN_AT_BOUNDARY',partition='evaluation')
        if path.endswith('/alerts'):
            items=[] if params.get('partition') in {'reference','development'} or mode=='missing' else [alert]
            return dict(items=items,total=len(items))
        if path.endswith('/findings'):return dict(items=[],total=0)
        if path.endswith('/evidence'):
            items=[dict(ordinal=393,recording_id='record-393',source_time='2004-02-12T10:33:39',role='ENTRY_FIRST',score_value=6.5,score_status='SCORABLE',rms=.165)]
            return dict(items=items,total=1)
        record_id=path.split('/recordings/')[1].split('/')[0]
        ordinal=int(record_id.split('-')[1]);record=next(row for row in rows if row['ordinal']==ordinal)
        return dict(raw_integrity='VERIFIED',recording=dict(source_time=record['expected_source_time'],source_sha256='c'*64),
            features=[dict(rms=.17,std_population=.05,absolute_peak=.25,crest_factor=2.,quality_status='VALID')],scores=[],baseline={'reference_health':'ASSUMED_NOT_LABELED'},receipt={'raw_preserved':True},
            signal={'points':[{'sample_index':0,'amplitude':.1},{'sample_index':1,'amplitude':-.1},{'sample_index':20479,'amplitude':.2}]})
    return get,calls


def select(app,label,value):
    next(widget for widget in app.selectbox if widget.label==label).set_value(value)
    return app.run(timeout=30)


def test_real_flow_filters_reference_and_component_scope(monkeypatch):
    get,calls=api_fixture();monkeypatch.setattr(client,'get',get)
    app=AppTest.from_file(Path.cwd()/'app.py').run(timeout=30)
    assert not app.exception and len(app.metric)>=4
    assert app.metric[0].value=='3/3' and app.metric[1].value=='2/3'
    select(app,'Partition','Reference')
    assert not app.exception and app.metric[0].value=='Fit only' and app.metric[1].value=='Not scored'
    select(app,'Component',2)
    assert any(path.endswith('/series') and params.get('channel')==2 for path,params in calls)
    assert all(BUILD in path for path,_ in calls if path!='/api/v1/evaluations')


def test_missing_capture_is_not_a_normal_signal(monkeypatch):
    get,_=api_fixture('missing');monkeypatch.setattr(client,'get',get)
    app=AppTest.from_file(Path.cwd()/'app.py').run(timeout=30)
    assert not app.exception and app.metric[0].value=='2/3'
    assert any('No admitted raw signal' in item.value for item in app.info)


def test_no_published_data_has_explicit_empty_state(monkeypatch):
    get,_=api_fixture('empty');monkeypatch.setattr(client,'get',get)
    app=AppTest.from_file(Path.cwd()/'app.py').run(timeout=30)
    assert not app.exception and len(app.metric)==0
    assert any('No frozen evaluation' in item.value for item in app.info)


def test_unavailable_api_does_not_display_zero_metrics(monkeypatch):
    get,_=api_fixture('unavailable');monkeypatch.setattr(client,'get',get)
    app=AppTest.from_file(Path.cwd()/'app.py').run(timeout=30)
    assert not app.exception and len(app.error)==1 and len(app.metric)==0


def test_corrupt_evidence_stops_before_charts_and_metrics(monkeypatch):
    get,_=api_fixture('corrupt');monkeypatch.setattr(client,'get',get)
    app=AppTest.from_file(Path.cwd()/'app.py').run(timeout=30)
    assert not app.exception and len(app.error)==1 and len(app.metric)==0
