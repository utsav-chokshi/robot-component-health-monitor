import runpy
import pytest
from sqlalchemy import create_engine
from fastapi.testclient import TestClient
from component_health import evaluation,health
from component_health.api import create_app
from component_health.settings import Settings

pytestmark=pytest.mark.integration


def test_version_bound_readers_and_integrity_fail_closed(migrated_database,tmp_path):
    engine=create_engine(migrated_database);analytics=tmp_path/'analytics';analytics.mkdir();raw=tmp_path/'raw';raw.mkdir()
    factory=runpy.run_path('tests/integration/test_evaluation_build.py')['full_synthetic_source']
    development=factory(engine,analytics);plan=evaluation.freeze(engine,analytics,development['build_id'])
    manifest,_=evaluation.build(engine,analytics,plan)
    settings=Settings(database_url=migrated_database,raw_directory=raw,analytical_directory=analytics)
    with TestClient(create_app(settings)) as client:
        base='/api/v1/evaluations/'+plan
        summary=client.get(base+'/summary');assert summary.status_code==200
        assert summary.json()['integrity']['status']=='VERIFIED'
        result=client.get(base+'/series?channel=1').json()
        assert result['total']==984 and len(result['items'])==984
        assert sum(row['score_status']=='NOT_APPLICABLE' for row in result['items'])==196
        assert client.get(base+'/series?channel=2&partition=evaluation&limit=100&offset=100').json()['total']==592
        for suffix in ['/series?channel=5','/series?limit=1001','/series?start=9&stop=2','/series?partition=unknown']:
            assert client.get(base+suffix).status_code==422
        alerts=client.get(base+'/alerts?channel=1').json();assert alerts['total']==1
        alert=alerts['items'][0]['alert_id']
        evidence=client.get(base+f'/alerts/{alert}/evidence?limit=1').json()
        assert evidence['total']==2 and len(evidence['items'])==1
        assert client.get(base+'/alerts/00000000-0000-0000-0000-000000000000/evidence').status_code==404
        assert client.get('/api/v1/evaluations/'+'f'*64+'/summary').status_code==404
        assert client.get('/api/v1/evaluations/not-a-hash/summary').status_code==422
        record=result['items'][0]['recording_id']
        assert client.get(base+f'/recordings/{record}?channel=1').status_code==503  # fixture has no original raw bytes
        assert client.get(base+f'/recordings/{record}/signal?max_points=10000').status_code==422
        assert client.get(base+'/findings?channel=1').json()['total']==0
        path=analytics/'evaluation-builds'/plan/'features.parquet';original=path.read_bytes();path.write_bytes(b'corrupt')
        response=client.get(base+'/series?channel=1')
        assert response.status_code==409 and 'items' not in response.json()
        assert str(tmp_path) not in response.text
        path.write_bytes(original)
        assert client.get(base+'/summary').status_code==200
        path.unlink()
        assert client.get(base+'/summary').status_code==503
    engine.dispose()
