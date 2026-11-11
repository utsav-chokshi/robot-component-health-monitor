# Raw ingestion and replay

The receiver processes IMS experiment 2 as independent recordings in declared source-time order. It does not concatenate files or produce features/health scores. The committed 984-file inventory is SHA-256 bound; changing it requires an explicit release/configuration decision. The source directory is mounted read-only and must already exist.

## Prepare the input explicitly

Obtain the IMS bearing dataset from the [NASA repository](https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/). The recorded archive URL is `https://phm-datasets.s3.amazonaws.com/NASA/4.+Bearings.zip`. This command downloads only when explicitly invoked:

```powershell
New-Item -ItemType Directory -Force downloads | Out-Null
curl.exe --fail --location --output 'downloads/4. Bearings.zip' 'https://phm-datasets.s3.amazonaws.com/NASA/4.+Bearings.zip'
(Get-FileHash -Algorithm SHA256 'downloads/4. Bearings.zip').Hash.ToLower()
```

Require archive SHA-256 `21001ac266c465f5d345ec42d7b508c6a6328487fd9d4d7774422dd5ea10ad83` before extraction. With an archive tool supporting ZIP, 7z and RAR, extract only `4. Bearings/IMS.7z`, then `2nd_test.rar` and the original documentation, then the `2nd_test` directory. Do not point the receiver at the archive or a parent directory. The intermediate 7z hash is `c6925060e702708afc47261d349339d7cd97a98166161da6b29c377eb3e66f2c`; second-test RAR hash is `b154d5ba1ae5f7f01cdd4f1bde5b08cfdc2f3134d51ad2f6614b1270a86ab632`. Preserve the original documentation/attribution. Extraction is currently a manual preparation prerequisite; no automatic download/extraction occurs on application startup or replay.

## Ingest and inspect

From the repository root, after initializing the private environment:

```powershell
$env:RCHM_INPUT_DIRECTORY = 'C:/absolute/path/to/2nd_test'
docker compose up --build -d --wait api
docker compose --profile ingestion run --rm receiver
Invoke-RestMethod 'http://localhost:8020/api/v1/recordings?limit=100&offset=0'
Invoke-RestMethod 'http://localhost:8020/api/v1/findings?limit=100&offset=0'
docker compose --profile ingestion run --rm replay
```

Use the container image built above; after source changes rebuild before rerunning workers. Pagination limits are 1–200 and offset 0–10000. Only the selected release/experiment is supported. Catalog responses describe admission history, not a fresh byte-integrity audit. Replay revalidates all retained evidence and fails on missing/corrupt objects. Historical findings stay visible after a later valid delivery; they are not a declaration that the current accepted input is invalid. No HTTP route executes ingestion, maintenance or model actions.

The receiver/replay services have two CPUs and a 1 GiB memory limit, use the same raw volume and serialize with a Linux filesystem lock. They run as UID 10001. The API mounts raw evidence read-only; PostgreSQL remains internal.

## Admission and durability

Every scheduled recording has a stable UUID5 identity excluding content hash. All expected rows are seeded in the catalog, including missing input. Valid input must match its expected hash/size, contain 20,480 rows of four finite numeric values, and pass bounded regular-file admission. Filenames carry source-local timestamps with UNSPECIFIED timezone, not UTC. Native amplitude units remain unverified.

Original bytes at most 2 MiB are stored by SHA-256. The immutable receipt envelope contains the versioned recording receipt, frozen inventory digest and channel-quality findings. Files are fsynced and atomically published without replacement before a database projection transaction. Existing objects are verified, never overwritten. The lock covers raw publication and DB projection. This is single-host process recovery, not distributed exactly-once or host-power-loss certification.

- Same identity and bytes: reuse one receipt and one logical catalog input. Delivery counts are command summaries, not durable transport-attempt events.
- Changed hash under an expected identity: CONFLICT; preserve admitted changed bytes, record hash/structural reasons, retain any previous accepted receipt.
- Matching expected bytes with invalid measurements: QUARANTINED. Such fixtures are synthetic tests; accepted real inventory files pass validation.
- Missing, oversized, symlink/nonregular input: REJECTED metadata receipt; no false raw-preservation claim. Files over the cap are not read into memory.
- Constant channels: raw recording may be accepted, but channel-specific ZERO_VARIATION_CHANNEL findings are retained for feature eligibility in Step 7.
- Unexpected names and malformed timestamp-like names are never opened. Bounded metadata findings under `/raw/admission-findings` store a name hash plus reason, without inventing a source timestamp or recording identity. They are separate from receipt-based HTTP findings.

Receipts are at `/raw/receipts/<receipt-id>.json`; original objects at `/raw/objects/<sha256>`. These paths are internal implementation details, not caller-controlled artifact endpoints. Preserve the raw volume independently from the PostgreSQL projection. Replay requires raw evidence plus the committed inventory, but does not require the original input directory. Stale `.pending-*` files from a killed process are unpublished and ignored. Orphan content objects are retained, not automatically deleted.

Strict receipt/envelope validation and recomputed source identities, hashes, shape and findings precede projection, including replay. A process interrupted after receipt preservation can replay without a second logical input. Replay of a partially damaged store may project valid receipts before reaching the error; it is not an all-or-nothing dataset publication. Publication and full recovery evaluation remain later steps.

## Scope of verification

Tests cover three deliveries, conflicts without replacement, missing/oversized/unsafe paths, malformed/nonfinite/empty data, timestamp-name issues, constant channels, chronology, interruption/replay, missing/corrupt bytes, tampered receipts, inventory digest binding, migration gates and API pagination. Tests isolate PostgreSQL from the application. The real-data run reuses a previously verified local extraction; it is not a fresh download/extraction demonstration. See [validation](validation.md) for measured results.
