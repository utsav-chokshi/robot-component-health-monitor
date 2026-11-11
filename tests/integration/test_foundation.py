import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,text
from sqlalchemy.exc import DBAPIError,IntegrityError
from component_health.api import create_app
from component_health.settings import Settings

pytestmark=pytest.mark.integration


def make_settings(database,tmp_path):
    raw=tmp_path/'raw';analytics=tmp_path/'analytics';raw.mkdir();analytics.mkdir()
    return Settings(database_url=database,raw_directory=raw,analytical_directory=analytics)


def test_explicit_migration_upgrade_downgrade_and_readiness(fresh_database,tmp_path):
    cfg=Config('alembic.ini')
    with TestClient(create_app(make_settings(fresh_database,tmp_path))) as client:
        assert client.get('/healthz').status_code==200
        assert client.get('/readyz').status_code==503
        command.upgrade(cfg,'head')
        assert client.get('/readyz').json()=={'status':'ready','contract_version':'1.0.0','migration_head':'0005_evaluation','scope':'foundation'}
        command.downgrade(cfg,'base')
        assert client.get('/readyz').status_code==503
        command.upgrade(cfg,'head')
        assert client.get('/readyz').status_code==200


@pytest.mark.parametrize('mutation',["UPDATE platform_metadata SET contract_schema_version='future' WHERE singleton_id=1","UPDATE alembic_version SET version_num='unexpected_revision'"])
def test_wrong_contract_or_migration_is_not_ready(migrated_database,tmp_path,mutation):
    engine=create_engine(migrated_database)
    try:
        with engine.begin() as connection:connection.execute(text(mutation))
        with TestClient(create_app(make_settings(migrated_database,tmp_path))) as client:
            assert client.get('/healthz').status_code==200
            response=client.get('/readyz')
            assert response.status_code==503 and response.json()['reason']=='contract_or_migration_mismatch'
    finally:engine.dispose()


def test_missing_storage_is_not_ready(migrated_database,tmp_path):
    value=make_settings(migrated_database,tmp_path)
    value.raw_directory.rmdir()
    with TestClient(create_app(value)) as client:
        response=client.get('/readyz')
        assert response.status_code==503 and response.json()['reason']=='database_or_storage_unavailable'


def test_api_database_transactions_are_read_only(migrated_database,tmp_path):
    app=create_app(make_settings(migrated_database,tmp_path))
    with TestClient(app) as client:
        assert client.get('/readyz').status_code==200
        with app.state.engine.connect() as connection:
            assert connection.scalar(text('SHOW transaction_read_only'))=='on'
            with pytest.raises(DBAPIError):connection.execute(text("UPDATE platform_metadata SET contract_schema_version='changed'"))
        assert client.get('/readyz').status_code==200


def test_contract_marker_singleton_is_enforced(migrated_database):
    engine=create_engine(migrated_database)
    try:
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:connection.execute(text("INSERT INTO platform_metadata(singleton_id,contract_schema_version) VALUES(2,'1.0.0')"))
        with engine.connect() as connection:assert connection.scalar(text('SELECT count(*) FROM platform_metadata'))==1
    finally:engine.dispose()
