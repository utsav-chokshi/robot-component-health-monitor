# Development health indicators and alert logic

This stage fits a separate reference-only RMS median/MAD baseline for each bearing channel and scores only the development partition. The final evaluation partition is deliberately NOT_EVALUATED until Step 9. The agent remains disabled.

## Run from a reviewed feature build

After the [feature pipeline](features.md), inspect `/api/v1/feature-builds` and choose the exact frozen build to use. From the source repository root:

```powershell
docker compose --profile pipeline build api pipeline
docker compose up -d --wait api
Invoke-RestMethod 'http://localhost:8020/api/v1/feature-builds?limit=20&offset=0'
$featureBuild = 'YOUR_REVIEWED_64_HEX_FEATURE_BUILD_ID'
docker compose --profile pipeline run --rm pipeline python -m component_health.health --feature-build $featureBuild
docker compose --profile pipeline run --rm pipeline python -m component_health.health --feature-build $featureBuild --recompute
Invoke-RestMethod 'http://localhost:8020/api/v1/health-builds?limit=20&offset=0'
```

The health stage is an explicit batch command in the existing pipeline image; it does not start a Prefect server. The feature stage's Prefect flow remains available separately. No new dependencies, model runtime, source downloads or maintenance actions are required. The worker retains the two-CPU/1-GiB limit and read-only raw mount. Health registry HTTP reads are bounded (limit 1–100); they expose build manifests, not per-row Parquet queries or a dashboard.

## Reference-only fit

For each channel, retain the complete declared reference set and each exclusion. Use only VALID, eligible reference features with finite positive RMS. At least 32 eligible reference windows are required; the full experiment declares 196 per channel. Compute `m = median(RMS)`, `MAD = median(abs(RMS-m))`, `s = 1.4826*MAD`. Parameters retain native_numeric amplitude units, unverified physical scaling and the ASSUMED_NOT_LABELED reference flag. Reference data is not certified healthy, and other channels are not labeled healthy controls.

Insufficient reference data, zero MAD or invalid scale yields an unusable baseline. No epsilon or future observations repair it. The baseline artifact retains the exact used feature IDs/RMS values and excluded slot IDs/reasons. Its stable identity depends on reference evidence and versioned policy, not development/evaluation measurements.

## Scores and coverage

An eligible development RMS `r` gets `abs(r-m)/s`, with ABOVE_REFERENCE, BELOW_REFERENCE or AT_REFERENCE direction. The score is a dimensionless robust deviation, not a fault probability, risk percentage or remaining life. Missing/bad features and unusable baselines produce null values with reasons, never zero in place of a missing observation. Scoring quality findings use the SCORING scope, separate from source/feature findings; multiple scoped reasons must not be counted as distinct failed recordings.

For the full inventory, coverage retains all 3,936 slots: 784 reference slots are NOT_APPLICABLE / REFERENCE_FIT_ONLY; all 784 development slots have score rows, including unscorable ones; 2,368 evaluation slots are NOT_EVALUATED and have no score IDs. Expected timestamps on unavailable input remain marked in the joined recording metadata. Source and alert timestamps have UNSPECIFIED timezone and must not be interpreted as UTC.

## Alert state machine

- Enter after two consecutive valid scheduled scores >=6. The first is high evidence; the second is activation. Values below 6 reset pending entry.
- While active, close after three consecutive valid scheduled scores <4. A score >=4 resets clearance. Middle values preserve an active alert.
- Missing/unscorable slots or unexpected ordinal/600-second spacing reset streaks. They keep an active alert open and mark its historical coverage interrupted. Current unavailable data is not a normal state.
- Each partition/channel starts fresh. A trailing incomplete entry streak is retained in coverage/summary, not fabricated into an alert. An active alert at the boundary remains OPEN_AT_BOUNDARY; no artificial closing observation is added.

Coverage stores causal per-slot state, streaks, transitions and uncertainty. Closing slots have no active-alert reference; their clearance evidence links to the closed alert. Evidence rows identify first high, activation, persistence, clearance candidates, final clearance and gaps. Closed alerts retain historical interruption flags even after later valid clearance. Expected timestamp gaps and alert spans are not continuous failure/alarm durations.

## Versioned bundle and recovery

The source feature bundle must match its registered manifest and pass integrity checks. A development-health identity binds that exact manifest, algorithm/configuration/schema and environment locks. It produces seven typed tables (recordings, features, scores, alerts, alert_evidence, findings, coverage), a baselines JSON artifact and a manifest with row/file/logical checksums. This is a DEVELOPMENT_HEALTH build, not final evaluation publication.

The worker writes and verifies a staging directory, atomically promotes it, then registers migration-0004 health metadata. Repeat builds reuse verified immutable content; `--recompute` calculates and checks a fresh complete bundle against it. Interrupted registration after promotion can resume without duplicate rows. Corrupt source, baseline or analytical bytes fail verification. A health build never overwrites the input feature bundle. Registry times are audit metadata, excluded from deterministic content identities.

Tests independently check the median/MAD formula, minimum-reference and exclusion handling, zero/invalid scale, overflow, missing inputs, threshold boundaries, repeated episodes, interrupted streaks, partition reset, causal prefix invariance, future-data independence, exact rebuilds, registration recovery, configuration rejection and corruption. See [observed verification](validation.md). Targeted forced-stop and fresh application-volume recovery passed; see [reproduction and scope](reproduction.md).

A threshold excursion is evidence of reference-relative signal change under a fixed heuristic. It does not diagnose a defect or establish safe/unsafe operation. False alarms, precision/recall/F1, exact warning lead time and RUL remain unsupported by the dataset labels. Step 9 will freeze and evaluate the chronological protocol without tuning on evaluation observations.
