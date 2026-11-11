from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from component_health.api import create_app
from component_health.settings import Settings


def settings(tmp_path):
    raw=tmp_path/'raw';analytics=tmp_path/'analytics';raw.mkdir();analytics.mkdir()
    return Settings(database_url='postgresql+psycopg://fixture:do-not-leak@invalid/test',raw_directory=raw,analytical_directory=analytics)


def test_liveness_does_not_query_database(tmp_path):
    engine=MagicMock();engine.connect.side_effect=RuntimeError('Database unavailable')
    with TestClient(create_app(settings(tmp_path),engine)) as client:
        response=client.get('/healthz')
        assert response.status_code==200 and response.json()['status']=='alive'
    engine.connect.assert_not_called()


def test_readiness_database_error_is_generic(tmp_path):
    engine=MagicMock();engine.connect.side_effect=RuntimeError('postgresql://fixture:do-not-leak@internal-host/db')
    with TestClient(create_app(settings(tmp_path),engine)) as client:
        response=client.get('/readyz')
        assert response.status_code==503
        assert response.json()=={'status':'not_ready','reason':'database_or_storage_unavailable'}
        assert 'do-not-leak' not in response.text and 'internal-host' not in response.text


def test_foundation_does_not_advertise_unimplemented_features(tmp_path):
    engine=MagicMock()
    with TestClient(create_app(settings(tmp_path),engine)) as client:
        payload=client.get('/api/v1/platform').json()
        assert payload['stage']=='dashboard'
        assert payload['capabilities']['ingestion'] is True
        assert payload['capabilities']['features'] is True
        assert payload['capabilities']['detector'] is True
        assert payload['capabilities']['dashboard'] is True
        assert payload['capabilities']['agent'] is False
        assert client.post('/api/v1/maintenance',json={'action':'replace'}).status_code==404
    engine.connect.assert_not_called()


def test_database_url_is_required_and_redacted(monkeypatch):
    monkeypatch.delenv('RCHM_DATABASE_URL',raising=False)
    with pytest.raises(ValidationError):Settings()
    value=Settings(database_url='postgresql+psycopg://fixture:do-not-leak@invalid/test')
    assert 'do-not-leak' not in repr(value)
