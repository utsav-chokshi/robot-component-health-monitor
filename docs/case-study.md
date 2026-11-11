# From sensor recordings to inspectable component-health evidence

Component monitoring requires more than an anomaly score. An operator or analyst needs to know which measurements produced it, whether inputs were valid, which baseline was used, and whether the same result can be rebuilt after a processing failure.

This project implements that evidence path using IMS bearing experiment 2: 984 industrial test-rig recordings, four channels and 3,936 recording/channel feature windows. It is a component-data case study relevant to robotics; no physical robot or robot telemetry is represented.

## Preserve first, project second

The receiver validates a fixed source inventory and preserves content-addressed original bytes plus immutable receipts before database projection. Stable logical identities allow changed bytes to become conflicts while retaining accepted evidence. Replay needs only the preserved raw volume, so database recovery does not depend on the original input directory or repeat a physical experiment.

A targeted forced stop demonstrated the boundary: 25 receipts existed while only 24 projections had committed. Replay recovered the missing projection; a second replay added zero and changed no receipt bytes. Resumed ingestion completed all 984 accepted recordings. Lifetime delivery-attempt counts remain unmeasured because there is no durable transport-attempt journal.

## Version the analytical experiment

Each recording/channel is an independent, nonoverlapping window. NumPy calculates RMS, population standard deviation, absolute peak and crest factor. Prefect orchestrates a separately locked worker; typed Parquet and DuckDB checks enforce row counts, identities, coverage and lineage before atomic publication.

Reference-only median/MAD calibration uses the first 196 recordings, with an explicitly assumed reference rather than a healthy label. Development uses the next 196; evaluation uses the final 592. A frozen plan binds source, split, baseline, policy, definitions, code and environment before evaluation scoring. Missing/unscorable values retain reasons and cannot become healthy zero scores.

## Make the result inspectable

FastAPI exposes bounded, build-specific reads after verifying artifact checksums and registered metadata. Streamlit shows scope/coverage, feature/score trends, causal alert evidence, quality and source provenance. Raw inspection independently validates the original recording and admission receipt; bounded min/max previews retain signal extrema.

The dashboard reads only the API and carries no database credentials or evidence-volume mounts. Readiness, catalog admission history, published-artifact integrity and individual raw verification are distinct claims.

## Verify recovery and reproducibility

The final local suites passed 67 core/API and five dashboard tests. An independent Docker project with empty application volumes reproduced all three analytical manifests and final metrics. Four targeted Docker SIGKILL stops recovered without duplicate build registrations; Prefect recomputation matched the frozen features. Missing and corrupted raw/receipt/Parquet evidence was rejected, verified restoration recovered reads, and a real database outage recovered without restarting the API.

The outage exercise also exposed inconsistent catalog error handling. A small API fix and regression test now make database unavailability a generic HTTP 503 across catalog routes. The original analysis modules and three frozen dependency locks were preserved.

## Interpret the result within the data

All 3,936 feature windows were eligible; all 3,152 operational development/evaluation slots were scorable. Evaluation retained four reference-relative, open-boundary alert episodes, one per channel. The dataset does not establish reliable per-record healthy/fault-onset labels, exact failure timing or physical amplitude scaling. These excursions do not diagnose four defects or establish other bearings as healthy controls.

Pipeline correctness and reproducibility are measured; fault-prediction suitability remains unestablished. Prepared data/base images/dependency caches were reused. Fresh-host acquisition/extraction, exhaustive interruption/power-loss cases, other architectures, production authorization and remote CI outcomes remain unverified. A guarded agent is a [future enhancement](future-enhancements.md).

Start with the [README](../README.md), [reproduction guide](reproduction.md), [demo](demo.md) and [measured results](evaluation-results.md).
