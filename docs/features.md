# Versioned recording features

This stage builds recording metadata, features, quality findings and coverage as typed Parquet tables. It does not fit a baseline or publish scores/alerts. Feature-only builds have their own registry and `FEATURES_ONLY` manifest; they are not the final seven-table health publication.

## Run

After [ingestion](ingestion.md), from the source repository root:

```powershell
docker compose --profile pipeline build api pipeline
docker compose up -d --wait api
docker compose --profile pipeline run --rm pipeline
Invoke-RestMethod 'http://localhost:8020/api/v1/feature-builds?limit=20&offset=0'
```

The default command uses a Prefect flow and a temporary internal Prefect server. It does not start a permanent Prefect UI, expose port 4202, or require a cloud account/API key. Flow telemetry is disabled. The worker has two CPUs/1 GiB and mounts original raw evidence read-only and analytical storage writable. Peak process RSS reported by the command excludes the temporary server's separate process; the container limit covers both.

The command prints a snapshot hash and build ID. To recompute from that exact frozen snapshot rather than the current catalog:

```powershell
$snapshot = 'COPY_THE_64_CHARACTER_SNAPSHOT_HASH_HERE'
docker compose --profile pipeline run --rm pipeline python -m component_health.features --snapshot $snapshot --recompute
```

This second command runs the same core directly without starting Prefect. Omit `--recompute` to verify and reuse an existing complete bundle, after rechecking its frozen raw evidence. Add `--orchestrated` for a Prefect-managed rebuild. No command downloads source data or runs a model. Stop services normally without deleting volumes.

## Features and quality

Each complete 20,480-sample recording/channel is one window, [0,20480), float64, no overlap or concatenation. The four features are RMS, population standard deviation (`ddof=0`), absolute peak and peak/RMS crest factor. Amplitude units are `native_numeric`, unverified physical scaling; crest factor is dimensionless. Captures retain source-local timestamps with UNSPECIFIED timezone. Nominal within-record sampling is 20,000 Hz, with the documented one-second versus 1.024-second discrepancy flagged.

Independent small reference fixtures test the formulas at rtol 1e-9/atol 1e-12. These are calculation tolerances, not sensor accuracy. Constant channels remain UNSCORABLE, including zero RMS with null crest factor. Overflow/nonfinite derived outputs become null with explicit reasons; underflow is also flagged. Invalid/missing recordings get no measured feature rows. Every expected recording/channel still has a coverage row with explicit reasons and a null feature link where unavailable. A feature is eligible only if its quality status is VALID. No missing value is filled with a normal zero score.

Each feature links through recording ID/channel/window/source SHA to its admitted receipt and source timestamp. The feature ID includes the committed feature-definition digest, which binds configuration, schema, numerical kernel and NumPy version. Component IDs are stable per dataset release/experiment/channel. Recording metadata distinguishes observed filename time from expected manifest time for missing inputs. Coverage contains 3,936 slots for the full inventory, split 784 reference/784 development/2,368 evaluation. Feature extraction fits nothing and may process all partitions; later calibration must use reference-only eligible rows.

## Frozen inputs and deterministic builds

Freeze takes a shared receiver lock and consistent PostgreSQL snapshot. It verifies catalog identity/time/ordinal/hash relationships and raw-versus-projected receipts, then retains the complete selection and semantic receipt content in an immutable snapshot. An interrupted ingestion with unprojected evidence must be replayed first. Receipt observation times are audit metadata and do not affect semantic input identity. Later arrivals create a new snapshot; rebuilding an old snapshot ignores them.

Build identity binds the frozen snapshot, feature-definition/schema digest, implementation source digests, Python/package versions and hashed environment locks. Unsupported feature configurations fail explicitly; changes require implemented/versioned behavior. Each Parquet table has explicit column types, row order, physical checksum, row count and logical checksum. Logical hashes include fixed column types, normalized nulls and exact float64 hexadecimal representations (signed zero normalized).

DuckDB checks recording/slot denominators, unique IDs, channel layout, feature/window/recording/coverage relationships and eligibility. Files are written and verified in a temporary staging directory, promoted atomically as a complete directory, and then registered in PostgreSQL. Rebuilding computes fresh files and checks identical manifests/checksums before reusing an existing build. Existing complete builds are never overwritten. If registration is interrupted after promotion, retry verifies the bundle and registers it once. Missing/corrupt raw or analytical evidence fails verification; it is not silently regenerated over an existing version. Targeted forced-stop and fresh application-volume validation passed; see [reproduction and scope](reproduction.md).

Internal analytical paths are `/analytics/snapshots/<sha>.json` and `/analytics/feature-builds/<build-id>/`. HTTP returns bounded registry metadata, not arbitrary filesystem paths or SQL execution. The four-table feature bundle is deliberately separate from later baseline/score/alert publication.

## Environment

The pipeline image extends the small API environment with a separate hash-locked set for NumPy, PyArrow, DuckDB and JSON Schema validation. Prefect runs in its own isolated virtual environment with a compatible FastAPI version and launches the core worker as a child process; it does not replace the application API dependencies. `scripts/lock-pipeline.py` regenerates Linux/amd64 CPython 3.12 wheel hashes in the pinned Python image, constrained to existing runtime/test versions. It must run with the source root mounted at `/workspace`. `scripts/lock-orchestration.py` separately locks the Prefect environment. Image installation uses hashes and dependency consistency checks. Remote CI is configured to install all three environments; remote results are not yet observed.

Formula/API references: [NumPy population standard deviation](https://numpy.org/doc/stable/reference/generated/numpy.std.html), [Apache Arrow documentation](https://arrow.apache.org/docs/), [DuckDB documentation](https://duckdb.org/docs/), [Prefect flows](https://www.prefect.io/docs/v3/concepts/flows). See [local verification](validation.md) for observed results and limitations.
