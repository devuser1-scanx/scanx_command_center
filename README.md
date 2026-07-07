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

## Local Setup and Commands

Run commands from the repository root:

```powershell
cd C:\Latitude_Projects\ScanX\Command_Center\scanx_command_center
```

Create and activate the virtual environment:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

Install application and development dependencies:

```powershell
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements-dev.txt
```

Create the local environment file:

```powershell
copy .env.example .env
```

The default local database URL is:

```text
postgresql+psycopg://scanx:scanx@localhost:5432/scanx_command_center
```

For local test-style runs, use a reachable PostgreSQL database and a JWT secret with at least
32 characters:

```powershell
$env:APP_ENV = "test"
$env:DEBUG = "false"
$env:DATABASE_URL = "postgresql+psycopg://test_user:test_password@localhost:5432/test_db"
$env:JWT_SECRET_KEY = "test-secret-key-for-local-that-is-long-enough"
$env:CORS_ALLOWED_ORIGINS = "http://localhost:5173,http://localhost:3000"
```

Run database migrations:

```powershell
alembic upgrade head
```

Run Ruff formatting and lint checks:

```powershell
ruff format .
ruff check . --fix
ruff check .
```

Run tests:

```powershell
python -m pytest
```

Run Python dependency audits:

```powershell
python -m pip install pip-audit
pip-audit -r requirements.txt
pip-audit
```

Run the development server:

```powershell
uvicorn app.main:app --reload
```

The API will be available at:

```text
http://localhost:8000
```

Before pushing code to `dev`, `main`, or production-related branches, run the full local
pre-deployment check:

```cmd
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%cd%\predeploy-check.ps1"
```

The pre-deployment script runs dependency installation, Ruff, Alembic migrations, pytest,
security scans, Docker build checks, image scanning, and a local `/health` smoke test.

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
