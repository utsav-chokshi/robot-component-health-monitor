# Future enhancements

The sensor-data pipeline, frozen evaluation and read-only evidence dashboard are implemented. The following extensions are not accepted application capabilities.

## Guarded investigation agent

Add an optional local, open-source model that can select bounded evidence reads and produce an advisory investigation with validated citations. Require retrieval of trend, quality, baseline and source evidence before the final response; validate referenced IDs and quantities in application code. The agent must not alter scores/alerts, execute maintenance or infer failure times unsupported by the dataset.

An initial Qwen3 1.7B pilot fit local resources but did not pass its evidence-retrieval and grounding gate. No model runtime or weights are integrated. A revised prompt/controller and, if needed, another model require a new frozen evaluation and a passing gate before integration. The data workflow remains independent of inference.

## Stronger component evidence

Evaluate data with defensible healthy/fault-onset labels, documented physical units and exact timing. Add independently held-out runs/components before claiming generalization. Any revised policy must be frozen before its new evaluation; do not tune against this published case study. Extend beyond one bearing experiment only with explicit source/channel contracts and separate provenance.

## Broader reproducibility and recovery

Verify a clean host, archive acquisition/extraction and dependency-cache builds; test other supported architectures with their own wheel locks. Extend interruption testing to additional staging/write boundaries and controlled host-power-loss scenarios. Add a durable delivery-attempt journal before reporting lifetime duplicate-attempt metrics.

## Deployment hardening

Introduce separate least-privilege database roles, authentication and authorization before exposing services beyond loopback. Add explicit backup/restore verification, retention policy and broader resource/load measurements. Remote CI is configured; publish its outcome only after an observed run.
