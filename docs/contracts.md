# Implemented ingestion and future design contracts

contracts/v1 contains the Step 4 strict JSON schemas/examples, initial condition-monitoring configuration and complete source-bound split manifest. They preserve source-local timestamps with unspecified timezone, native amplitude units, typed dimensionless scores, raw-preservation/admission state and a disabled guarded-agent design.

Recording receipt admission and replay are implemented. Feature formulas, quality/coverage relationships and feature-only publication are implemented and tested. Development score/alert state is implemented. Frozen chronological evaluation publication is now implemented; model claim support remains future work. Schema validity alone cannot prove those behaviors.

The split references the exact accepted source inventory/archive digests. Examples have synthetic identities/numerical values and are not a joined observed application dataset. Null unscorable scores must not be converted to normal zero values. No hours-to-failure field or model maintenance action is permitted.
## Recording receipt implementation

Recording receipts now have strict Pydantic/envelope validation plus independently recomputed identity, content hash, structural checks and channel-quality findings on initial projection and replay. Fixture receipts are checked against the committed receipt JSON schema. The exact source inventory is included and hash-bound. Feature rows now undergo runtime schema validation and lineage checks; score rows now receive runtime schema and relationship validation; agent schemas remain design references. See [ingestion behavior](ingestion.md).





## Dashboard readers

Build-specific bounded read routes now expose frozen features, scores, alerts, quality and source lineage. Integrity failures stop the view; reference fit-only and unscorable states remain distinct from normal scores. Raw previews validate original bytes and receipts. See [reader bounds and status codes](dashboard.md).
