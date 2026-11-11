# Development foundation

Python 3.12 on Linux/amd64 is the tested dependency target. requirements.in pins direct runtime dependencies and is also the package's dynamic dependency source. requirements-test.in adds test dependencies; requirements-build.in pins packaging tools. Their .lock files include exact resolved wheel hashes. The test resolution is constrained to the runtime versions.

Regenerate deliberately from the pinned base image, from the repository root:

```powershell
docker run --rm -v "${PWD}:/workspace" -w /workspace python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df python scripts/lock-dependencies.py
```

The resolver requires internet and its pip resolver is the one in that pinned image. Runtime/test installations require hashes and the Docker build runs pip check. Do not update dependencies implicitly alongside features. The current locks cover only introduced foundation/build/test dependencies; Prefect, analytics, dashboard and model environments are future component work.

Migrations are explicit `python -m alembic upgrade head` jobs. API startup does not create tables. The initial migration establishes the singleton contract-version marker. Readiness requires exactly the expected Alembic revision and contract version plus readable volume paths. Domain schemas are added through later migrations.

API transactions default to PostgreSQL read-only, and no data-write/maintenance routes exist. The local bootstrap database owner is still shared with migration configuration; dedicated least-privilege database roles are an implementation hardening task, not a current production-security claim. The API image runs as a non-root user. Local interfaces bind to localhost.

RCHM_TEST_DATABASE_URL must point to the declared isolated test service or its CI equivalent. Fixtures refuse unexpected hosts/users/password configurations and create only rchm_test_<random UUID> databases. Cleanup drops only each exact created database. Never supply a production/shared database URL. The application service uses RCHM_DATABASE_URL; tests do not read its live .env.

The GitHub Actions workflow is configured, not proof of a remote run. Source is local until separately connected/published. Preserve raw data in its existing parent validation cache; do not copy it into this repository or Docker context. No dataset or model is needed to run foundation checks.
