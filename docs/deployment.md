# Deployment Notes

This repository is prepared for GitHub Actions deployments to Google Cloud Run.

## Required GCP Resources

- Cloud Run service
- Cloud SQL PostgreSQL instance
- Artifact Registry Docker repository
- Secret Manager secrets for database password and JWT secret
- GitHub Actions deployer service account
- Workload Identity Federation provider for GitHub

## Required GitHub Variables

```text
GCP_PROJECT_ID=your-gcp-project-id
GCP_REGION=us-central1
CLOUD_RUN_SERVICE=scanx-command-center-api
ARTIFACT_REPO=scanx-docker-repo
IMAGE_NAME=scanx-command-center-api
CLOUDSQL_INSTANCE=your-project:us-central1:scanx-postgres-db
DATABASE_NAME=scanx_app
DATABASE_USER=postgres
```

## Required GitHub Secrets

```text
GCP_WORKLOAD_IDENTITY_PROVIDER=projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/POOL/providers/PROVIDER
GCP_SERVICE_ACCOUNT=github-actions-deployer@your-gcp-project-id.iam.gserviceaccount.com
```

## Required Secret Manager Secrets

```text
scanx-db-password
scanx-jwt-secret
scanx-prod-database-url
```

`scanx-prod-database-url` is the read-only connection string to the existing
production ScanX database (appointments/clinics/patients) that the dashboard,
clinics, and patients features read from - see `app/db/prod_session.py`. It is
injected as `PROD_DATABASE_URL` via `--set-secrets` on both the staging and
production Cloud Run deploy steps. Because `--set-secrets`/`--set-env-vars`
fully replace a revision's configuration rather than merging with it, any
value set by hand outside this workflow (e.g. via the console) will be wiped
out on the next deploy - it must stay defined here to persist.

## Pipeline Flow

1. Pull requests to `main` run tests and security checks.
2. Pushes to `develop` run tests and security checks.
3. Pushes to `main` run tests, security checks, build, image scan, push, deploy, and smoke test.

## Cloud SQL Runtime Connection

The workflow attaches the Cloud SQL instance to Cloud Run and injects:

```text
DATABASE_HOST=/cloudsql/${CLOUDSQL_INSTANCE}
DATABASE_NAME=${DATABASE_NAME}
DATABASE_USER=${DATABASE_USER}
DATABASE_PASSWORD=scanx-db-password:latest
```

The application builds the PostgreSQL connection URL at runtime when a full `DATABASE_URL` is not
provided.
