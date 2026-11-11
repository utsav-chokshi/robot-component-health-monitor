"""Single-host raw-first IMS receiver. No feature extraction or health inference."""
import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import time
import resource
from uuid import UUID, uuid5
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sqlalchemy import text

NAMESPACE = UUID('3e4b4937-ad67-565c-a2fc-d0cd52310466')
INVENTORY_SHA = '04deccd0803fe16e262c8ae7539859463677c97338760b9fd41455339c20bdec'
RELEASE = 'ims-nasa-archive-21001ac266c4'
CAP = 2 * 1024 * 1024
FILENAME = re.compile(r'\d{4}(?:\.\d{2}){5}')


class Receipt(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    schema_version: Literal['1.0.0']
    receipt_id: str
    recording_id: str
    dataset_release: Literal['ims-nasa-archive-21001ac266c4']
    experiment: Literal['ims-test-2']
    source_key: str
    source_time: str
    source_timezone: Literal['UNSPECIFIED']
    received_at_utc: str
    expected_sha256: str
    source_sha256: str | None
    size_bytes: int = Field(ge=0)
    raw_preserved: bool
    artifact_sha256: str | None
    admission_status: Literal['ACCEPTED', 'QUARANTINED', 'CONFLICT', 'REJECTED']
    sample_rows: int | None = Field(ge=0)
    channels: int | None = Field(ge=0)
    reason_codes: list[str]


class ChannelFinding(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    channel: int = Field(ge=1, le=4)
    reason_code: Literal['ZERO_VARIATION_CHANNEL']


class Envelope(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    receipt: Receipt
    channel_findings: list[ChannelFinding]
    inventory_sha256: Literal['04deccd0803fe16e262c8ae7539859463677c97338760b9fd41455339c20bdec']
    validation_version: Literal['ims-receiver-v1']


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def identity(value):
    return str(uuid5(NAMESPACE, canonical(value).decode()))


def recording_id(name):
    return identity({'dataset_release': RELEASE, 'experiment': 'ims-test-2', 'source_key': '2nd_test/' + name})


def load_manifest(path):
    data = path.read_bytes()
    if sha(data) != INVENTORY_SHA:
        raise ValueError('Inventory digest mismatch')
    rows = json.loads(data)
    validate_manifest(rows)
    if len(rows) != 984:
        raise ValueError('Expected 984 recordings')
    return rows


def validate_manifest(rows):
    names = []
    for row in rows:
        name = row['path']
        if not FILENAME.fullmatch(name):
            raise ValueError('Invalid inventory filename')
        source_time = datetime.strptime(name, '%Y.%m.%d.%H.%M.%S').isoformat()
        if row['source_timestamp'] != source_time or not re.fullmatch('[0-9a-f]{64}', row['sha256']):
            raise ValueError('Invalid inventory time/hash')
        if row['samples'] != 20480 or row['channels'] != 4 or not 0 < row['bytes'] <= CAP:
            raise ValueError('Invalid inventory shape/size')
        names.append(name)
    if len(names) != len(set(names)):
        raise ValueError('Duplicate inventory identities')


def durable_write(path, data):
    """Publish without replacement; verify an existing content-addressed object."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError('Immutable evidence mismatch')
        return False
    fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError('Immutable evidence mismatch')
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)
    return True


@contextmanager
def receiver_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'receiver.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def parse_measurements(data):
    reasons, findings = [], []
    rows, columns = 0, None
    low, high = [math.inf] * 4, [-math.inf] * 4
    try:
        for line in data.decode('ascii').splitlines():
            tokens = line.split()
            rows += 1
            if len(tokens) != 4:
                reasons.append('INVALID_COLUMN_COUNT')
                columns = len(tokens)
                continue
            if columns is None:
                columns = 4
            try:
                values = [float(token) for token in tokens]
            except ValueError:
                reasons.append('NON_NUMERIC_VALUE')
                continue
            if not all(math.isfinite(value) for value in values):
                reasons.append('NONFINITE_VALUE')
                continue
            for channel, value in enumerate(values):
                low[channel] = min(low[channel], value)
                high[channel] = max(high[channel], value)
    except UnicodeDecodeError:
        reasons.append('INVALID_ENCODING')
    if rows != 20480:
        reasons.append('INVALID_ROW_COUNT')
    if not reasons:
        findings = [{'channel': channel + 1, 'reason_code': 'ZERO_VARIATION_CHANNEL'}
                    for channel in range(4) if low[channel] == high[channel]]
    return rows, columns, sorted(set(reasons)), findings


def bounded_read(directory, name):
    """No caller paths, symlink following, FIFOs or unbounded reads."""
    dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
        except FileNotFoundError:
            return None, 0, ['MISSING_SOURCE']
        except OSError:
            return None, 0, ['UNSAFE_SOURCE_TYPE']
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                return None, 0, ['UNSAFE_SOURCE_TYPE']
            if info.st_size > CAP:
                return None, info.st_size, ['SIZE_LIMIT_EXCEEDED']
            with os.fdopen(fd, 'rb', closefd=False) as source:
                data = source.read(CAP + 1)
            if len(data) > CAP:
                return None, len(data), ['SIZE_LIMIT_EXCEEDED']
            return data, len(data), []
        finally:
            os.close(fd)
    finally:
        os.close(dir_fd)


def make_envelope(row, data, size, admission_reasons):
    digest = sha(data) if data is not None else None
    rows, columns, reasons, channel_findings = None, None, list(admission_reasons), []
    if data is not None:
        rows, columns, numerical_reasons, channel_findings = parse_measurements(data)
        reasons.extend(numerical_reasons)
        if digest != row['sha256']:
            reasons.append('HASH_MISMATCH')
        if size != row['bytes']:
            reasons.append('SIZE_MISMATCH')
    status = ('REJECTED' if data is None else 'CONFLICT' if digest != row['sha256']
              else 'QUARANTINED' if reasons else 'ACCEPTED')
    receipt = dict(schema_version='1.0.0', recording_id=recording_id(row['path']), dataset_release=RELEASE,
                   experiment='ims-test-2', source_key='2nd_test/' + row['path'],
                   source_time=row['source_timestamp'], source_timezone='UNSPECIFIED',
                   expected_sha256=row['sha256'], source_sha256=digest, size_bytes=size,
                   raw_preserved=data is not None, artifact_sha256=digest,
                   admission_status=status, sample_rows=rows, channels=columns, reason_codes=sorted(set(reasons)))
    receipt['receipt_id'] = identity(receipt)
    receipt['received_at_utc'] = datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
    return {'receipt': receipt, 'channel_findings': channel_findings,
            'inventory_sha256': INVENTORY_SHA, 'validation_version': 'ims-receiver-v1'}


def validate_evidence(envelope, row, root):
    """Recompute source validation and identity before every DB admission/replay."""
    Envelope.model_validate(envelope)
    receipt = envelope['receipt']
    for field in ['source_sha256', 'artifact_sha256']:
        if receipt[field] is not None and not re.fullmatch('[0-9a-f]{64}', receipt[field]):
            raise ValueError('Invalid artifact digest')
    if type(receipt['size_bytes']) is not int or receipt['size_bytes'] < 0:
        raise ValueError('Invalid size')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z', receipt['received_at_utc']):
        raise ValueError('Invalid receipt time')
    datetime.fromisoformat(receipt['received_at_utc'].replace('Z', '+00:00'))
    path = root / 'objects' / (receipt['artifact_sha256'] or 'absent')
    data = None
    if receipt['raw_preserved']:
        data, _, reasons = bounded_read(root / 'objects', path.name)
        if reasons == ['MISSING_SOURCE']:
            raise FileNotFoundError('Preserved object missing')
        if reasons:
            raise ValueError('Preserved object admission failed')
    expected = make_envelope(row, data, receipt['size_bytes'], receipt['reason_codes'] if data is None else [])
    expected['receipt']['received_at_utc'] = receipt['received_at_utc']
    # Rejected evidence has no bytes to revalidate; restrict its allowed states.
    if data is None and (receipt['reason_codes'] not in [['MISSING_SOURCE'], ['UNSAFE_SOURCE_TYPE'], ['SIZE_LIMIT_EXCEEDED']]):
        raise ValueError('Invalid rejected evidence')
    if data is not None and len(data) != receipt['size_bytes']:
        raise ValueError('Preserved object size mismatch')
    if expected != envelope:
        raise ValueError('Evidence identity/content mismatch')


def seed_catalog(engine, manifest):
    with engine.begin() as connection:
        for ordinal, row in enumerate(sorted(manifest, key=lambda value: value['source_timestamp']), 1):
            connection.execute(text('''INSERT INTO recordings(recording_id, source_key, source_time, ordinal, expected_sha256)
                VALUES (:id,:key,:time,:ordinal,:sha) ON CONFLICT (recording_id) DO NOTHING'''),
                dict(id=recording_id(row['path']), key='2nd_test/' + row['path'], time=row['source_timestamp'], ordinal=ordinal, sha=row['sha256']))
        actual = connection.execute(text('SELECT source_key,expected_sha256 FROM recordings ORDER BY source_key')).all()
        if actual != sorted([('2nd_test/' + row['path'], row['sha256']) for row in manifest]):
            raise ValueError('Catalog inventory differs from receiver manifest')


def project(engine, envelope):
    receipt = envelope['receipt']
    with engine.begin() as connection:
        inserted = connection.execute(text('''INSERT INTO recording_receipts(receipt_id,recording_id,admission_status,evidence)
            VALUES (:id,:recording,:status,CAST(:evidence AS jsonb)) ON CONFLICT (receipt_id) DO NOTHING RETURNING receipt_id'''),
            dict(id=receipt['receipt_id'], recording=receipt['recording_id'], status=receipt['admission_status'], evidence=canonical(envelope).decode())).first()
        if not inserted:
            existing = connection.scalar(text('SELECT evidence FROM recording_receipts WHERE receipt_id=:id'), {'id': receipt['receipt_id']})
            if existing != envelope:
                raise ValueError('Database evidence differs from immutable receipt')
        if inserted and receipt['admission_status'] == 'ACCEPTED':
            connection.execute(text('UPDATE recordings SET accepted_receipt_id=:receipt WHERE recording_id=:id'),
                               dict(receipt=receipt['receipt_id'], id=receipt['recording_id']))
        return bool(inserted)


def ingest(engine, source, root, manifest, *, after_receipt=None):
    validate_manifest(manifest)
    counts = Counter()
    with receiver_lock(root):
        seed_catalog(engine, manifest)
        for row in sorted(manifest, key=lambda value: value['source_timestamp']):
            data, size, reasons = bounded_read(source, row['path'])
            envelope = make_envelope(row, data, size, reasons)
            receipt = envelope['receipt']
            if data is not None:
                durable_write(root / 'objects' / sha(data), data)
            path = root / 'receipts' / (receipt['receipt_id'] + '.json')
            if path.exists():
                envelope = json.loads(path.read_bytes())
                counts['duplicate_deliveries'] += 1
            else:
                durable_write(path, canonical(envelope))
            validate_evidence(envelope, row, root)
            if after_receipt:
                after_receipt(envelope)
            counts['projected_new'] += int(project(engine, envelope))
            counts[receipt['admission_status']] += 1
        # Record unexpected names as bounded identities; never open them or guess timestamps.
        expected = {row['path'] for row in manifest}
        for entry in os.scandir(source):
            if entry.name not in expected:
                code = 'UNEXPECTED_SOURCE_NAME'
                if FILENAME.fullmatch(entry.name):
                    try:
                        datetime.strptime(entry.name, '%Y.%m.%d.%H.%M.%S')
                    except ValueError:
                        code = 'INVALID_SOURCE_TIME'
                finding = {'reason_code': code, 'name_sha256': sha(os.fsencode(entry.name))}
                durable_write(root / 'admission-findings' / (identity(finding) + '.json'), canonical(finding))
                counts['unexpected_names'] += 1
    return dict(counts)


def replay(engine, root, manifest):
    validate_manifest(manifest)
    counts = Counter()
    inventory = {'2nd_test/' + row['path']: row for row in manifest}
    with receiver_lock(root):
        seed_catalog(engine, manifest)
        for path in sorted((root / 'receipts').glob('*.json')):
            envelope = json.loads(path.read_bytes())
            receipt = envelope['receipt']
            if path.name != receipt['receipt_id'] + '.json':
                raise ValueError('Receipt filename mismatch')
            validate_evidence(envelope, inventory[receipt['source_key']], root)
            counts['verified_receipts'] += 1
            counts['projected_new'] += int(project(engine, envelope))
    return dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['ingest', 'replay'])
    parser.add_argument('--input', type=Path, default=Path('/input'))
    parser.add_argument('--raw', type=Path, default=Path('/raw'))
    parser.add_argument('--manifest', type=Path, default=Path('/app/contracts/v1/source-file-inventory.json'))
    args = parser.parse_args()
    from component_health.database import build_engine
    from component_health.settings import Settings
    engine = build_engine(Settings())
    try:
        started = time.perf_counter()
        manifest = load_manifest(args.manifest)
        result = ingest(engine, args.input, args.raw, manifest) if args.operation == 'ingest' else replay(engine, args.raw, manifest)
        result['elapsed_seconds'] = round(time.perf_counter() - started, 3)
        result['worker_peak_rss_mib'] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 3)
        print(json.dumps(result, sort_keys=True))
    except Exception:
        # Preserve private paths/credentials in library errors; CLI has a generic failure surface.
        parser.exit(1, 'Receiver failed; evidence was not overwritten. Check input, migration and integrity.\n')
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
