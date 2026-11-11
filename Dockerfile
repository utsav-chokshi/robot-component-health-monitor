FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.in requirements.lock requirements-build.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements-build.lock && pip install --no-cache-dir --require-hashes -r requirements.lock
COPY pyproject.toml README.md alembic.ini ./
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY contracts/ ./contracts/
RUN pip install --no-cache-dir --no-deps --no-build-isolation . && pip check && useradd --uid 10001 --create-home app && install -d -o 10001 -g 10001 /raw /analytics
USER app
CMD ["uvicorn", "component_health.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

FROM app AS pipeline
USER root
COPY requirements-pipeline.lock ./
RUN --mount=type=cache,target=/root/.cache/pip pip install --require-hashes -r requirements-pipeline.lock && pip check
COPY requirements-orchestration.lock ./
RUN --mount=type=cache,target=/root/.cache/pip python -m venv /opt/orchestrator && /opt/orchestrator/bin/pip install --require-hashes -r requirements-orchestration.lock && /opt/orchestrator/bin/pip check
USER app
CMD ["python", "-m", "component_health.features"]

FROM pipeline AS tests
USER root
COPY requirements-test.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements-test.lock && pip check
COPY tests/ ./tests/
COPY contracts/ ./contracts/
USER app
CMD ["pytest", "-q", "-p", "no:cacheprovider"]

FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS dashboard
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-dashboard.lock ./
RUN --mount=type=cache,target=/root/.cache/pip pip install --require-hashes -r requirements-dashboard.lock && pip check && useradd --uid 10001 --create-home app
COPY dashboard/ /app/
USER app
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]

FROM dashboard AS dashboard-tests
USER root
COPY requirements-dashboard-test.lock ./
RUN --mount=type=cache,target=/root/.cache/pip pip install --require-hashes -r requirements-dashboard-test.lock && pip check
COPY tests-dashboard/ /app/tests-dashboard/
USER app
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests-dashboard"]
