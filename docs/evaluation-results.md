# Frozen chronological evaluation results

Observed locally on 2026-11-10, Docker Desktop Linux/amd64, CPU only.

All **984/984 recordings**, **3,936/3,936 eligible feature slots** and **3,152/3,152 operational score slots** are represented. Reference: 196 recordings/784 fit-only slots. Development: 196 recordings/784 scores. Evaluation: 592 recordings/2,368 scores. All four reference baselines retain their original 196 used windows, zero exclusions and parameter bytes. These are coverage/computation checks, not prediction accuracy.

The plan was sealed at 2026-11-10T22:54:53.6325560+00:00 (UTC), before the first evaluation registration at 2026-11-10T22:58:32.6553780+00:00 (UTC). A separate pre-scoring check recorded zero evaluation builds. Baseline/policy/configuration, exact source/feature/receipt versions, split, metric definitions, code and environment were frozen. Evaluation did not fit or tune parameters. Every prior development score, event and causal coverage state is unchanged.

| Channel | Scorable slots | Threshold windows (>=6) | Alerts | Closed | Open at boundary |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 592/592 | 440/592 | 1 | 0 | 1 |
| 2 | 592/592 | 151/592 | 1 | 0 | 1 |
| 3 | 592/592 | 81/592 | 1 | 0 | 1 |
| 4 | 592/592 | 278/592 | 1 | 0 | 1 |
| all | 2368/2368 | 950/2368 | 4 | 0 | 4 |

The fixed policy yielded **4 evaluation alert episodes**, all open at the boundary, with no interrupted coverage. Threshold burden is 950/2368 valid observed windows. Active-associated window burden is 945/2368, measured after each transition and excluding closure/unavailable slots. These window counts do not represent continuous time under alarm. Development remains 784 valid scores with zero excursions/alerts.

Excursions occurred across all four channels. They establish reference-relative signal changes under this heuristic, without confirming fault type or asserting that channels 2–4 are healthy controls. Detector suitability is **NOT_ESTABLISHED**: reliable healthy/fault-onset labels, calibrated outcomes and exact failure timing are unavailable. False-alarm rate, precision/recall/F1, classification accuracy, warning lead time, failure probability and RUL are explicitly null / NOT_APPLICABLE. No final timestamp was substituted for fault onset. This is a single previously inspected experiment, not a blind/unseen-component test.

Duplicate delivery attempts are NOT_MEASURED; receipt uniqueness does not establish transport-attempt counts. Historical admission observations are bound to the frozen snapshot; coverage and exclusions use declared identities/slots. Numerical ratios with zero valid denominator are null / NOT_APPLICABLE, as verified by controlled cases.

Final suite: **65 passed, 1 warning in 61.58s (0:01:01)**. It includes frozen-plan/code/input checks, no-fit evaluation, zero-denominator/missing-input handling, partition-state reset, unchanged development results, exact rebuild, registration recovery and corruption rejection. All earlier ingestion/feature/health/Prefect isolation checks remain included. The one warning is a TestClient dependency deprecation. An independent score and episode oracle verified every operational score and all four real alert episodes. Source feature/raw/receipt hashes and baseline bytes remain unchanged.

Two complete builds (5.248 / 6.836 s; 285.035 / 276.684 MiB peak process RSS) matched every manifest, metric and physical/logical table checksum. They process existing features under the two-CPU/1-GiB worker limit; they are not raw-processing or fleet-throughput benchmarks. One evaluation registration exists; health/readiness/docs and read-only registry HTTP checks passed. Temporary test databases were removed and their service stopped.

Evaluation/plan ID: fc4bce1121224cabce204c79e4dc4d74f0062a22fa610110f90d34694ac63912
Development input: bbafc6eb40e692a23f183fd75f903a22d8055e084d8b4c114d59e6441e2f65b6
Baseline SHA-256: d9bcefd1c46442e8359144c276d1ef31010b9406e5300a80e913e68ab6e0b6ce
Policy SHA-256: ca0cc9e6a09fb0a2c670ab123add7884836398bd1d88db4feb3c0e13378578dd
Metric definition SHA-256: 9b330b30547e6e95539a9611018089ed7d0e5db546bbd7f7b1f4034a1fae65f6

| Table | Rows | Logical SHA-256 |
| --- | ---: | --- |
| recordings | 984 | df78823702aaa06cd71ff78e0a2933c03c229ecfb480bb61283fe99711ab1404 |
| features | 3936 | d1ddadc6c894f4a23d93c463039c296cf986b7de3b7056611aa992436f8f3f97 |
| scores | 3152 | 45878e582dc0a4fec6eb21b289d30ce0ac5a96d93db16caf49b6415c980a88ed |
| alerts | 4 | dfaa96a98fb6237389239ffbfce67cae203a7cabf1f17ae5082b70830fea3d39 |
| alert_evidence | 949 | 87e2b4066734a6bba7f7ce49bc4ba90171b194eb2d2fb13e4eccfc4a169ea2d1 |
| findings | 0 | 02394dc6756857c74406f059fb28a3e2b38e72b731e32add76e55da574e4e351 |
| coverage | 3936 | db9c54b9c25f3b27353cf12264a9158a444c6afd47c1cf651c56bcac133f7bbe |

Reproduce with the [evaluation guide](evaluation.md), [development calibration](health.md), [features](features.md) and [ingestion](ingestion.md). This version is preserved without tuning against these results. Native physical units/timezone and label limitations remain. Cached source/images were reused; no fresh-host/download/extraction or hard-kill recovery claim. Linux/amd64 locks only; remote CI configured but unobserved. At the Step 9 observation, the API exposed registry/metric metadata. Step 10 subsequently added the [dashboard and bounded per-row readers](dashboard.md) without changing these frozen results. Local bootstrap-owner authorization is not production role separation. The agent remains disabled pending a new passing pilot.
