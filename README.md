# ScanX Command Center

ScanX Command Center is a centralized operations platform for ScanX clinics. This repository
currently contains the preliminary FastAPI backend scaffold plus CI/CD files for deploying to
Google Cloud Run with Cloud SQL PostgreSQL.

## Backend Stack

- FastAPI
- SQLAlchemy
- Alembic
- PostgreSQL / Cloud SQL
- Docker
- GitHub Actions
- Google Cloud Run

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env
uvicorn app.main:app --reload
```

The default local database URL is:

```text
postgresql+psycopg://scanx:scanx@localhost:5432/scanx_command_center
```

Run checks:

```powershell
ruff check .
pytest
alembic upgrade head
```

## Health Endpoints

- `GET /health` returns application liveness and is used by the Cloud Run smoke test.
- `GET /ready` checks database connectivity.

## GitHub Actions Configuration

Create these repository variables under `Settings > Secrets and variables > Actions > Variables`:

```text
GCP_PROJECT_ID
GCP_REGION
CLOUD_RUN_SERVICE
ARTIFACT_REPO
IMAGE_NAME
CLOUDSQL_INSTANCE
DATABASE_NAME
DATABASE_USER
```

Create these repository secrets:

```text
GCP_WORKLOAD_IDENTITY_PROVIDER
GCP_SERVICE_ACCOUNT
```

Store production runtime secrets in GCP Secret Manager:

```text
scanx-db-password
scanx-jwt-secret
```

The workflow in `.github/workflows/ci-cd.yml` runs linting, tests, Alembic migrations,
open-source security checks, Docker image scanning, Artifact Registry push, Cloud Run deploy,
and a `/health` smoke test.
