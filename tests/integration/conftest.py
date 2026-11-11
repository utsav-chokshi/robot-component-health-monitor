import os,re,uuid
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url


@pytest.fixture
def fresh_database(monkeypatch):
    value=os.environ.get('RCHM_TEST_DATABASE_URL')
    if not value:pytest.skip('Dedicated PostgreSQL test service not configured')
    url=make_url(value)
    if url.host not in {'test-postgres','localhost','127.0.0.1','::1'} or url.username!='rchm_test_admin' or url.database!='postgres' or url.password not in {'isolated-local-test-only','isolated-ci-test-only'}:
        pytest.fail('Refusing database setup outside the declared isolated test configuration',pytrace=False)
    admin=create_engine(url,isolation_level='AUTOCOMMIT')
    name='rchm_test_'+uuid.uuid4().hex
    assert re.fullmatch(r'rchm_test_[0-9a-f]{32}',name)
    with admin.connect() as connection:connection.execute(text(f'CREATE DATABASE {name}'))
    database=url.set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv('RCHM_DATABASE_URL',database)
    try:yield database
    finally:
        # Delete only the exact temporary database created by this fixture.
        with admin.connect() as connection:connection.execute(text(f'DROP DATABASE {name} WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def migrated_database(fresh_database):
    command.upgrade(Config('alembic.ini'),'head')
    return fresh_database
