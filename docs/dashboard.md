# Component-health evidence dashboard

The Streamlit dashboard reads one selected frozen evaluation through FastAPI. It has no database credentials or raw/analytical volume mounts. The API verifies the selected plan, manifest, table/sidecar checksums and registered projections before returning bounded data. Raw recording inspection separately verifies original bytes and the admission receipt.

## Run

After [frozen evaluation](evaluation.md):

```powershell
docker compose up --build -d --wait dashboard
```

Open [the dashboard](http://localhost:8502) and [API documentation](http://localhost:8020/docs). Ports bind to loopback only. Set DASHBOARD_PORT in private .env before startup if 8502 is occupied. Starting an empty stack does not download/process data; an explicit empty state directs the user to the reproduction guide.

Stop without deleting persisted project volumes:

```powershell
docker compose --profile '*' down
```

Do not add --volumes unless intentionally discarding retained evidence. The dashboard's root filesystem is read-only, with temporary scratch paths only. It runs as UID 10001 under a 768-MiB limit; API is 512 MiB. The API reuses the existing pipeline image for its pinned DuckDB reader libraries and overrides its command to Uvicorn. It does not start Prefect/model inference. The original runtime/pipeline/orchestration locks and frozen analysis modules are unchanged; dashboard dependencies have separate hashed locks.

## Inspect the evidence

1. Choose the evaluated build, bearing/channel, partition and capture range. Every data request is bound to that build. Top counters describe this selected scope; fit-only reference windows are not counted as failed scores.
2. Trends show independent recording/channel points for RMS, population standard deviation, absolute peak or crest factor. Reference/development/evaluation regions remain distinct. Capture order is used on the axis so unspecified source-local timestamps are not converted to browser UTC/local time. Missing inputs are not interpolated.
3. The score chart retains entry/clear thresholds and optional compressed scaling to show small and large deviations, including zero. Reference captures remain fit-only; missing/unscorable values are not replaced with zero. Reference calibration is an assumption, not a healthy label.
4. Alert evidence shows first high, activation, latest processed capture, closure/boundary status and interrupted coverage. Evidence is paginated at 100 rows; each page can drill into its recording/feature/score/baseline/receipt lineage. Episode spans are not continuous failure/alarm durations.
5. Data quality retains explicit unavailable/unscorable slots, reasons and scoped findings. Unavailable-label metrics remain marked NOT_APPLICABLE. API errors or failed artifact checks stop the data view rather than displaying invented zero metrics.
6. Provenance inspects a selected raw capture and its frozen features. A min/max bucket preview shows at most 1,024 points from 20,480 samples for the selected channel, preserving global extrema. It is not the full sample trace. Sample index is the axis; nominal 20-kHz and one-second/1.024-second timing uncertainty remain disclosed. Downloaded window metadata includes its evaluation build ID.

All four channels have reference-relative evaluation excursions in the selected case. These do not diagnose four defects or establish channels 2–4 as healthy controls. Physical amplitude scaling and source timezone remain unverified. The interface has no maintenance execution or agent controls.

## Reader bounds and integrity

Routes live under `/api/v1/evaluations/{build_id}/`: `summary`, `series`, `alerts`, `alerts/{alert_id}/evidence`, `findings`, `recordings/{recording_id}` and `recordings/{recording_id}/signal`. Build IDs are 64 lowercase hex; entity IDs are UUIDs. Series supports one channel (1–4), known partition, ordinal bounds 1–984, limit 1–1000 and bounded offset. Other lists cap at 200; signal previews cap at 2048 points. SQL/table/column expressions and filesystem paths are never caller inputs.

Unknown entities are 404; invalid filters are 422; checksum/projection failures are 409; unavailable evidence is 503. Readers use read-only PostgreSQL transactions and read-only artifact mounts, two concurrent reader slots, one DuckDB thread and 128-MiB query memory. Table hashes are checked on each read without stale integrity caching. This checks publication artifacts; individual raw files are revalidated on inspection. Targeted failure/recovery and local resource checks passed in Step 11; see [reproduction and scope](reproduction.md). Production role separation remains unfinished.

## Test

```powershell
docker compose --profile test run --build --rm tests
docker compose --profile test run --build --rm --no-deps dashboard-tests
docker compose --profile test stop test-postgres
```

Core tests exercise version-bound filtering, pagination, missing/foreign IDs and corruption rejection. Isolated Streamlit AppTest cases exercise filters, reference-only/missing/empty/unavailable states. The live stack additionally checks real signal checksums, peak-preserving previews, full evidence pagination and unchanged frozen recomputation. See [observed verification](validation.md). Remote CI is configured but unobserved. API reference: [Streamlit AppTest](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest).
