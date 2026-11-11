# Three-minute evidence walkthrough

Prerequisite: complete the [reproduction guide](reproduction.md) through frozen evaluation. Start `docker compose up -d --wait dashboard`, then open http://localhost:8502. At compact widths, collapse the overlay sidebar after choosing filters. This walkthrough inspects existing results; it never executes maintenance or changes scores.

| Time | Action | What to explain |
| --- | --- | --- |
| 0:00–0:35 | Choose the evaluated IMS build, Bearing 1, All captures, range 1–984 | This is a real industrial bearing dataset, used as a robotics-relevant component case study. Coverage is 788/788 operational scores; 196 reference captures are fit-only. |
| 0:35–1:15 | On Trends, inspect RMS and reference-relative deviation | Each dot is an independent recording/channel window. Reference/development/evaluation regions stay separate. Entry needs two valid scores ≥6; clearance needs three <4. A large deviation does not diagnose a fault. |
| 1:15–1:55 | Open Alert evidence; select the open-boundary episode and inspect an evidence recording | Bearing 1 has 437 evidence rows across five pages. Distinguish first-high evidence from activation. The raw-byte/receipt verification badge connects the episode to retained source evidence. Boundary-open does not establish continuous failure duration. |
| 1:55–2:25 | Open Data quality, then Provenance | The selected full-data run has no unavailable feature slots. Missing/unscorable data remains explicit in controlled tests. Inspect the raw recording checksum, frozen features and min/max signal preview. Units/timezone and nominal sampling discrepancy remain disclosed. |
| 2:25–3:00 | Show the reproduction guide and verification summary | 67 core/API and five dashboard tests passed; four targeted forced-stop recoveries passed; fresh-volume analytical manifests/metrics matched. Prepared source/images were reused. The optional agent is future work. |

Optional: switch to Reference to show fit-only status, then Bearing 2 to show that all channels are inspected independently. Other channels are not labeled healthy controls. Return to Bearing 1 / All captures for the primary view.

Do not describe score coverage as detection accuracy, an alert as a confirmed defect, or the final timestamp as exact fault onset. The [case study](case-study.md), [evaluation results](evaluation-results.md) and [future enhancements](future-enhancements.md) provide the supporting context.
