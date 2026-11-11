import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import create_engine, text
from component_health.api import create_app
from component_health.ingestion import (CAP, ingest, replay, sha, recording_id, validate_manifest, load_manifest)
from component_health.settings import Settings

pytestmark = pytest.mark.integration


def fixture(tmp_path, data=None):
    source = tmp_path / 'input'; source.mkdir()
    raw = tmp_path / 'raw'; raw.mkdir()
    if data is None:
        data = (b'1 2 3 4\n2 3 4 5\n' * 10240)
    name = '2004.02.12.10.32.39'
    (source / name).write_bytes(data)
    row = dict(path=name, source_timestamp='2004-02-12T10:32:39', sha256=sha(data), bytes=len(data), samples=20480, channels=4)
    return source, raw, [row]


def counts(engine):
    with engine.connect() as connection:
        return [connection.scalar(text('SELECT count(*) FROM ' + table)) for table in ['recordings', 'recording_receipts']]


def test_three_deliveries_conflict_and_schema(migrated_database, tmp_path):
    engine = create_engine(migrated_database)
    source, raw, manifest = fixture(tmp_path)
    original = (source / manifest[0]['path']).read_bytes()
    for attempt in range(3):
        result = ingest(engine, source, raw, manifest)
        assert result['ACCEPTED'] == 1
        assert result['projected_new'] == int(attempt == 0)
    assert counts(engine) == [1, 1]
    (source / manifest[0]['path']).write_bytes(original.replace(b'1', b'9'))
    assert ingest(engine, source, raw, manifest)['CONFLICT'] == 1
    assert counts(engine) == [1, 2]
    assert (raw / 'objects' / sha(original)).read_bytes() == original
    with engine.connect() as connection:
        accepted = connection.scalar(text('SELECT accepted_receipt_id FROM recordings'))
        assert accepted is not None
        assert connection.scalar(text("SELECT admission_status FROM recording_receipts WHERE receipt_id=:id"), {'id': accepted}) == 'ACCEPTED'
    validator = Draft202012Validator(json.loads(Path('contracts/v1/schemas/recording-receipt.schema.json').read_text()), format_checker=FormatChecker())
    for path in (raw / 'receipts').glob('*.json'):
        validator.validate(json.loads(path.read_text())['receipt'])
    assert replay(engine, raw, manifest) == {'verified_receipts': 2, 'projected_new': 0}
    engine.dispose()


@pytest.mark.parametrize('data,reason', [
    (b'', 'INVALID_ROW_COUNT'),
    (b'1 2 3\n' * 20480, 'INVALID_COLUMN_COUNT'),
    (b'NaN 2 3 4\n' * 20480, 'NONFINITE_VALUE'),
    (b'inf 2 3 4\n' * 20480, 'NONFINITE_VALUE'),
    (b'bad 2 3 4\n' * 20480, 'NON_NUMERIC_VALUE'),
    (b'\xff 2 3 4\n' * 20480, 'INVALID_ENCODING')])
def test_changed_invalid_data_preserved_but_not_accepted(migrated_database, tmp_path, data, reason):
    engine = create_engine(migrated_database)
    source, raw, manifest = fixture(tmp_path)
    (source / manifest[0]['path']).write_bytes(data)
    assert ingest(engine, source, raw, manifest)['CONFLICT'] == 1
    receipt = json.loads(next((raw / 'receipts').glob('*.json')).read_text())['receipt']
    assert reason in receipt['reason_codes'] and receipt['raw_preserved'] is True
    assert (raw / 'objects' / sha(data)).read_bytes() == data
    with engine.connect() as connection:
        assert connection.scalar(text('SELECT accepted_receipt_id FROM recordings')) is None
    engine.dispose()


def test_matching_hash_malformed_input_is_quarantined(migrated_database, tmp_path):
    engine = create_engine(migrated_database)
    source, raw, manifest = fixture(tmp_path, b'1 2 3\n')
    assert ingest(engine, source, raw, manifest)['QUARANTINED'] == 1
    engine.dispose()


@pytest.mark.parametrize('kind,reason', [('missing','MISSING_SOURCE'), ('oversize','SIZE_LIMIT_EXCEEDED'), ('symlink','UNSAFE_SOURCE_TYPE'), ('directory','UNSAFE_SOURCE_TYPE')])
def test_bounded_admission_and_missing_slots(migrated_database, tmp_path, kind, reason):
    engine = create_engine(migrated_database)
    source, raw, manifest = fixture(tmp_path)
    path = source / manifest[0]['path']; path.unlink()
    if kind == 'oversize': path.write_bytes(b'x' * (CAP + 1))
    if kind == 'symlink': path.symlink_to('/etc/passwd')
    if kind == 'directory': path.mkdir()
    assert ingest(engine, source, raw, manifest)['REJECTED'] == 1
    receipt = json.loads(next((raw / 'receipts').glob('*.json')).read_text())['receipt']
    assert receipt['reason_codes'] == [reason] and receipt['raw_preserved'] is False
    assert not (raw / 'objects').exists()
    assert counts(engine) == [1, 1]
    assert replay(engine, raw, manifest)['projected_new'] == 0
    engine.dispose()


def test_interruption_replay_integrity_and_catalog_api(migrated_database, tmp_path):
    engine = create_engine(migrated_database)
    source, raw, manifest = fixture(tmp_path)
    def stop(_): raise RuntimeError('Controlled interruption before projection')
    with pytest.raises(RuntimeError): ingest(engine, source, raw, manifest, after_receipt=stop)
    assert counts(engine) == [1, 0]
    assert replay(engine, raw, manifest) == {'verified_receipts': 1, 'projected_new': 1}
    analytical = tmp_path / 'analytics'; analytical.mkdir()
    with TestClient(create_app(Settings(database_url=migrated_database, raw_directory=raw, analytical_directory=analytical))) as client:
        response = client.get('/api/v1/recordings?limit=1').json()
        assert response['items'][0]['admission'] == 'ACCEPTED'
        assert response['source_timezone'] == 'UNSPECIFIED'
        assert client.get('/api/v1/recordings?limit=201').status_code == 422
        assert client.get('/api/v1/findings').json()['items'] == []
    object_path = raw / 'objects' / manifest[0]['sha256']
    original = object_path.read_bytes()
    object_path.write_bytes(original.replace(b'1', b'9'))
    with pytest.raises(ValueError): replay(engine, raw, manifest)
    object_path.unlink()
    with pytest.raises(FileNotFoundError): replay(engine, raw, manifest)
    engine.dispose()


def test_chronology_unknown_names_and_channel_quality(migrated_database, tmp_path):
    engine = create_engine(migrated_database)
    source, raw, manifest = fixture(tmp_path, b'1 2 3 4\n' * 20480)
    later = dict(manifest[0], path='2004.02.12.10.42.39', source_timestamp='2004-02-12T10:42:39')
    manifest.insert(0, later)  # intentionally unordered; later file is missing
    (source / 'not-a-time').write_bytes(b'ignored')
    (source / '2004.99.12.10.42.39').write_bytes(b'ignored')
    assert ingest(engine, source, raw, manifest)['unexpected_names'] == 2
    with engine.connect() as connection:
        ordered = connection.execute(text('SELECT source_key,ordinal FROM recordings ORDER BY ordinal')).all()
        assert ordered[0][0].endswith('10.32.39') and ordered[1][1] == 2
        envelope = connection.scalar(text("SELECT evidence FROM recording_receipts WHERE admission_status='ACCEPTED'"))
        assert len(envelope['channel_findings']) == 4
    findings = [json.loads(path.read_text()) for path in (raw / 'admission-findings').glob('*.json')]
    assert {item['reason_code'] for item in findings} == {'UNEXPECTED_SOURCE_NAME', 'INVALID_SOURCE_TIME'}
    with pytest.raises(ValueError): validate_manifest([dict(later, path='../escape')])
    with pytest.raises(ValueError): validate_manifest([dict(later, path='2004.99.12.10.42.39')])
    engine.dispose()


@pytest.mark.parametrize('mutation', ['artifact_path', 'boolean_size', 'unknown_field', 'receipt_time'])
def test_tampered_receipts_fail_closed(migrated_database, tmp_path, mutation):
    engine = create_engine(migrated_database)
    source, raw, manifest = fixture(tmp_path)
    ingest(engine, source, raw, manifest)
    path = next((raw / 'receipts').glob('*.json'))
    envelope = json.loads(path.read_text())
    if mutation == 'artifact_path': envelope['receipt']['artifact_sha256'] = '../../etc/passwd'
    if mutation == 'boolean_size': envelope['receipt']['size_bytes'] = True
    if mutation == 'unknown_field': envelope['receipt']['extra'] = 'unsupported'
    if mutation == 'receipt_time': envelope['receipt']['received_at_utc'] = 'yesterday'
    path.write_text(json.dumps(envelope))
    with pytest.raises(ValueError): replay(engine, raw, manifest)
    engine.dispose()


def test_committed_manifest_is_hash_bound(tmp_path):
    path = Path('contracts/v1/source-file-inventory.json')
    assert len(load_manifest(path)) == 984
    changed = tmp_path / 'inventory.json'
    changed.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError): load_manifest(changed)
