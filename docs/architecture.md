# Architecture

The application separates measurement preservation, transactional metadata, immutable analytics and evidence inspection. It is a single-host, CPU-oriented Docker system with explicit batch commands.

| Component | Responsibility | Access |
| --- | --- | --- |
| Receiver | Validate bounded files, preserve original bytes and immutable receipts, then project metadata | Read-only input directory; writable raw volume and catalog |
| Replay | Revalidate durable receipts/bytes and recover database projection | Raw volume and catalog; no original-input mount |
| PostgreSQL 17 / Alembic | Recording identities, receipt projections, build/plan registries and contract markers | Internal network; explicit migrations |
| Feature worker | Freeze semantic receipt/catalog snapshot; derive NumPy recording-local features; validate typed Parquet | Raw read-only; analytical volume writable; explicit batch execution |
| Prefect | Orchestrate feature execution through an isolated dependency environment | Temporary internal server; no persistent UI or cloud account |
| Health / evaluation worker | Fit reference-only parameters; freeze and apply policy; publish lineage and supported metrics | Existing versioned features; immutable analytical publication |
| FastAPI / DuckDB | Read selected-build metadata and checksum-verified, parameterized, bounded analytical rows | Read-only database transactions and evidence mounts |
| Streamlit / Altair | Display filters, feature/score trends, alerts, quality and verified waveform previews | API only; no database credentials/data mounts; read-only root filesystem |

## Evidence and identity

The fixed IMS inventory declares 984 recordings and four channels. Stable recording identities exclude content hashes so changed bytes can be retained as conflicts without replacing accepted evidence. Original content and immutable receipts precede projection; replay recovers durable but unprojected observations. Receipt audit timestamps remain separate from semantic analytical identity.

Each feature represents one recording/channel: 20,480 samples, no overlap or concatenation. Reference/development/evaluation membership remains chronological, with all channels of a recording kept together. Missing or unscorable slots retain reasons and null measurements/scores. Source-local timestamps remain marked UNSPECIFIED; physical amplitude units are not asserted.

Feature publication binds source snapshot, feature formulas, code and locked dependencies. Development health publication retains reference-only median/MAD parameters and explicit policy. The evaluation plan seals those inputs before scoring; evaluation never refits or tunes on reserved observations. Seven final typed tables retain recording, feature, score, alert, evidence, finding and coverage relationships.

Complete bundles are staged, verified, atomically promoted and then registered once. Retrying after promotion verifies the existing publication before registration. Existing content is not silently overwritten. [Recovery behavior and tested limits](reproduction.md).

## Runtime and reader bounds

The Compose project owns its PostgreSQL/raw/analytical volumes. Default host endpoints are loopback-only API `8020` and dashboard `8502`; PostgreSQL has no host port. API liveness is distinct from migration/storage readiness and per-evidence integrity. Database unavailability returns generic HTTP 503 without private connection details.

The API uses the pipeline image for its pinned DuckDB libraries, with an explicit Uvicorn command. The frozen runtime/pipeline/orchestration locks are preserved; dashboard packages use separate hashed locks. Models and maintenance execution are absent.

Readers bind every request to a registered 64-hex evaluation ID, verify the manifest/plan/sidecars and all published table hashes, then execute fixed parameterized queries. UUIDs, component channels, partitions, capture bounds, limits and offsets are validated. Two reader slots, one DuckDB thread and 128-MiB query memory bound inspection. Raw bytes and original receipts are separately checked on source inspection; waveform previews use bounded min/max buckets rather than every sample. [Routes, statuses and controls](dashboard.md).

Pipeline/receiver workers have two CPUs and a 1-GiB limit; API/PostgreSQL are limited to 512 MiB each, dashboard to 768 MiB. Local resource measurements are bounded smoke evidence, not a production capacity claim. Bootstrap-owner credentials and read-only transactions do not constitute production least-privilege role separation. [Validation](validation.md).
