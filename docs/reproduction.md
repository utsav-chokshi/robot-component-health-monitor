# Reproduce and verify the workflow

This guide starts with empty **application volumes** in a separate Compose project. Use Linux/amd64 containers and the committed dependency locks. The observed Windows/Docker run reused cached images and the already extracted, checksum-verified IMS experiment 2. It did not validate a fresh host, a new archive download/extraction, other CPU architectures or a clean dependency-cache build.

## Prepare

Follow [dataset preparation and ingestion prerequisites](ingestion.md). Keep archives, extracted data and `.env` private and outside Git. Initialize credentials with `./scripts/Initialize-LocalEnvironment.ps1`; preserve an existing `.env` when retaining database volumes. The exact source inventory is committed and checked at ingestion; it is not a substitute for dataset attribution or obtaining the source.

From the repository root in PowerShell, choose an unused project name and unused loopback ports:

```powershell
$env:RCHM_INPUT_DIRECTORY = 'C:/absolute/path/to/2nd_test'
$env:API_PORT = '18020'
$env:DASHBOARD_PORT = '18502'
docker compose -p rchm-check up --build -d --wait dashboard
Invoke-RestMethod http://localhost:18020/readyz
Invoke-RestMethod http://localhost:18020/api/v1/evaluations
```

A new name creates independent PostgreSQL, raw and analytical volumes. Reusing a name reuses its volumes; it is no longer a fresh-volume test. An empty evaluation catalog is expected before processing. Do not delete your existing project volumes to reproduce this check.

## Process

```powershell
docker compose -p rchm-check --profile ingestion run --rm receiver
docker compose -p rchm-check --profile ingestion run --rm replay
docker compose -p rchm-check --profile pipeline run --build --rm pipeline
$features = Invoke-RestMethod http://localhost:18020/api/v1/feature-builds
$featureBuild = $features.items[0].build_id
docker compose -p rchm-check --profile pipeline run --rm pipeline python -m component_health.health --feature-build $featureBuild
$health = Invoke-RestMethod http://localhost:18020/api/v1/health-builds
$development = $health.items[0].build_id
docker compose -p rchm-check --profile pipeline run --rm pipeline python -m component_health.evaluation freeze --development-build $development
```

Review the selected feature/development manifests before freezing. Copy the printed plan ID, then:

```powershell
$plan = 'COPY_RETURNED_64_HEX_PLAN_ID'
docker compose -p rchm-check --profile pipeline run --rm pipeline python -m component_health.evaluation build --plan $plan
docker compose -p rchm-check --profile pipeline run --rm pipeline python -m component_health.evaluation build --plan $plan --recompute
```

Open [the validation dashboard](http://localhost:18502). See [dashboard controls and bounds](dashboard.md), [feature rebuilds](features.md), [reference-only calibration](health.md) and [evaluation protocol](evaluation.md). With this exact release/code/locks, the observed IDs are:

| Artifact | Build ID |
| --- | --- |
| Features | `1637b79bde988201438686783b303f3ca2e6209a0b82bc4410f4299072494100` |
| Development health | `bbafc6eb40e692a23f183fd75f903a22d8055e084d8b4c114d59e6441e2f65b6` |
| Frozen evaluation | `fc4bce1121224cabce204c79e4dc4d74f0062a22fa610110f90d34694ac63912` |

Changed source, definitions, code or bound dependencies may legitimately require a different build; investigate rather than editing manifests to force a match. Audit observation/registration timestamps are separate from content identity.

## Recovery and integrity

Receiver receipts and original bytes precede database projection. If ingestion stops, run replay to recover durable but unprojected receipts, then repeat ingestion to process remaining source files. Replay has no source-directory mount. Repeated commands verify and reuse immutable content; they do not constitute distributed exactly-once delivery or a durable attempt log.

Feature, development and evaluation bundles are staged, atomically promoted and then registered. A stop after promotion can leave a complete unregistered bundle. Repeat the same command: it verifies and registers that bundle without replacing it. A stop before promotion may leave an unregistered staging directory; existing valid publications are not overwritten. The measured forced-stop campaign targeted specific durable boundaries, not every filesystem instruction or host power loss.

Missing raw/analytical evidence is unavailable, not a healthy zero value. Selected analytical checksum failures return HTTP 409; missing evidence returns 503. Raw inspection independently verifies bytes and receipts. Replay and feature reuse reject corrupt raw evidence. Preserve suspect files and restore only from a separately verified copy; do not hand-edit manifests/checksums or silently regenerate an existing publication. Readiness checks service/storage availability, not every retained byte.

Stop the separate stack while preserving its evidence:

```powershell
docker compose -p rchm-check --profile '*' stop
```

Keep the main project on its original ports. Clear the temporary environment variables or use a new PowerShell session before managing it. No `--volumes` cleanup is needed for these instructions. [Observed verification and limits](validation.md) distinguish pipeline correctness from unestablished fault-prediction suitability.
