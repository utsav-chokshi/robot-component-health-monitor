"""A database outage must stay a bounded, non-sensitive HTTP failure."""
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from component_health.api import create_app
from component_health.settings import Settings


def test_database_outage_is_unavailable_without_private_details(tmp_path):
    class UnavailableEngine:
        def connect(self):
            raise OperationalError('private-query',None,Exception('private-database-details'))
        def dispose(self):pass
    settings=Settings(database_url='postgresql+psycopg://unused:private-password@unused/unused',
                      raw_directory=tmp_path,analytical_directory=tmp_path)
    with TestClient(create_app(settings,engine=UnavailableEngine()),raise_server_exceptions=False) as client:
        assert client.get('/healthz').status_code==200
        for path in ['/readyz','/api/v1/evaluations','/api/v1/feature-builds','/api/v1/health-builds',
                     '/api/v1/recordings','/api/v1/findings','/api/v1/evaluations/'+'0'*64+'/summary']:
            response=client.get(path)
            assert response.status_code==503,path
            assert 'private' not in response.text and 'postgresql' not in response.text
