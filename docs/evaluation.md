# Frozen chronological evaluation

Evaluation reuses the exact Step 8 baseline artifact and policy. It never recalibrates parameters or tunes thresholds using evaluation observations. Development results remain immutable and are compared row-for-row with their original publication. `health.py` and its development-only guard are preserved; evaluation is a separate module.

## Freeze before scoring

After a reviewed [development-health build](health.md), inspect `/api/v1/health-builds` and choose its ID. From the source root:

```powershell
docker compose --profile pipeline build api pipeline
docker compose up -d --wait api
$development = 'YOUR_REVIEWED_64_HEX_DEVELOPMENT_BUILD_ID'
docker compose --profile pipeline run --rm pipeline python -m component_health.evaluation freeze --development-build $development
```

This operation performs input-integrity and protocol checks and writes/registers a `FROZEN_EVALUATION_PLAN`. It does not score evaluation. Copy the returned plan ID, then run:

```powershell
$plan = 'YOUR_FROZEN_64_HEX_PLAN_ID'
docker compose --profile pipeline run --rm pipeline python -m component_health.evaluation build --plan $plan
docker compose --profile pipeline run --rm pipeline python -m component_health.evaluation build --plan $plan --recompute
Invoke-RestMethod 'http://localhost:8020/api/v1/evaluations?limit=20&offset=0'
```

The plan seals the registered development/feature manifests, baseline bytes, policy/configuration, receipt snapshot, source inventory, chronological split, metric definitions, evaluation code and environment locks. Changed inputs/code/definitions require a new explicitly frozen plan. An existing plan or output is never overwritten. Freeze/registration timestamps are separate audit metadata and do not perturb content identity.

Input integrity checking recalculates the existing reference-only development result for verification; evaluation scoring itself consumes the frozen parameter bytes without fitting. Reference is assumed for calibration, not labeled healthy. The source was inspected for feasibility earlier, so this is a single-experiment chronological case study, not a blind test or unseen-robot generalization.

## Coverage and supported metrics

All 984 recordings and 3,936 channel slots remain in the final seven tables. Reference ordinals 1–196 (784 slots) stay fit-only. Development 197–392 (784 slots) and evaluation 393–984 (2,368 slots) get score rows, including explicit unscorable rows where inputs/baselines are unavailable. Alert state starts fresh per partition/channel; no development alert carries into evaluation.

The committed `contracts/v1/evaluation-metrics-v1.json` is frozen before scoring. The report supplies overall/partition source and feature coverage plus partition/channel operational groups:

- Score coverage: scorable slots / all declared operational slots, with unscorable counts and reason codes alongside.
- Threshold burden: valid scores >=6 / valid scored slots. The denominator does not silently discard unavailable inputs from coverage reporting.
- Active-window burden: valid slots carrying an active-alert reference after the transition / valid scored slots. It includes activation, excludes closing/unavailable slots, and is not continuous time under alarm.
- Alert activations, closures, unresolved boundary alerts, interrupted coverage, evidence links and trailing incomplete entry groups.
- Exact frozen baseline eligibility and used/excluded reference counts; historical receipt-admission observations.

Ratios with zero eligible denominator have null values and NOT_APPLICABLE status. Duplicate transport-attempt counts are NOT_MEASURED because prior commands emitted summaries rather than a durable delivery-attempt journal; receipt uniqueness must not be mislabeled as zero duplicate deliveries.

False-alarm rate, precision/recall/F1, fault-classification accuracy, warning lead time, failure probability and RUL have null values marked NOT_APPLICABLE. Healthy/fault-onset labels, calibrated outcomes and exact failure timing are unavailable. A threshold alert does not confirm a defect; absence of alerts does not prove health. Neither the final recording timestamp nor channels 2–4 are invented ground truth. Detector suitability remains NOT_ESTABLISHED under this label limitation, separately from pipeline correctness.

## Publication and verification

Each evaluation bundle contains the seven typed Parquet tables, identical baseline bytes, the frozen receipt snapshot, `metrics.json` and a checksummed manifest. Tables retain deterministic IDs, order, explicit types and logical/file hashes. All operational score/alert/coverage relationships and prior development results are rechecked. A complete staged directory is atomically promoted, then registered once. Interrupted registration can resume; corruption is rejected and existing content is never silently replaced. Recompute calculates a fresh complete bundle and compares the full manifest and checksums.

Internal paths are `/analytics/evaluation-plans/<plan>.json` and `/analytics/evaluation-builds/<plan>/`. HTTP exposes bounded registry/metric metadata, not arbitrary paths, SQL or execution. The worker uses the existing two-CPU/1-GiB pipeline image with raw read-only. This phase is a batch CLI; no Prefect/model server or new dependency is added. The [dashboard and bounded per-row readers](dashboard.md) are implemented; targeted forced-stop and fresh application-volume verification is recorded in [validation](validation.md). See [observed local verification](validation.md).
