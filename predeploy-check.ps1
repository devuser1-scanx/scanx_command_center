# predeploy-check.ps1
# Run all local CI/CD checks before pushing to GitHub
# Command to run this script using:
    # PowerShell: powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\predeploy-check.ps1
    # CMD: powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%cd%\predeploy-check.ps1"
$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

$TestJwtSecret = "test-secret-key-for-local-that-is-long-enough"

Write-Host ""
Write-Host "========================================"
Write-Host " ScanX Local Pre-Deployment Check"
Write-Host "========================================"
Write-Host ""

function Run-Step {
    param (
        [string]$Name,
        [scriptblock]$Command
    )

    Write-Host ""
    Write-Host "----------------------------------------"
    Write-Host "Running: $Name"
    Write-Host "----------------------------------------"

    & $Command

    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "FAILED: $Name" -ForegroundColor Red
        exit 1
    }

    Write-Host "PASSED: $Name" -ForegroundColor Green
}

# 1. Check virtual environment
if (-not (Test-Path "venv\Scripts\Activate.ps1")) {
    Write-Host "venv not found. Please create virtual environment first." -ForegroundColor Red
    exit 1
}

Write-Host "Activating virtual environment..."
& .\venv\Scripts\Activate.ps1

# 2. Check required files
$requiredFiles = @(
    "requirements.txt",
    "requirements-dev.txt",
    "Dockerfile",
    "alembic.ini"
)

foreach ($file in $requiredFiles) {
    if (-not (Test-Path $file)) {
        Write-Host "Missing required file: $file" -ForegroundColor Red
        exit 1
    }
}

# 3. Check Docker
Run-Step "Docker is running" {
    docker info | Out-Null
}

# 4. Install/update dependencies
Run-Step "Upgrade pip, setuptools, wheel" {
    python -m pip install --upgrade pip setuptools wheel
}

Run-Step "Install development dependencies" {
    python -m pip install -r requirements-dev.txt
}

Run-Step "Install local security tools" {
    python -m pip install bandit pip-audit semgrep
}

Run-Step "Check Python dependency conflicts" {
    python -m pip check
}

foreach ($tool in @("gitleaks", "trivy")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Host "$tool not found. Please install $tool and rerun this script." -ForegroundColor Red
        exit 1
    }
}

# 5. Start local PostgreSQL test DB
Write-Host ""
Write-Host "Checking local PostgreSQL test container..."

$containerExists = docker ps -a --format "{{.Names}}" | Select-String -Pattern "^scanx-test-postgres$"

if ($containerExists) {
    Write-Host "scanx-test-postgres container exists. Starting it..."
    docker start scanx-test-postgres | Out-Null
} else {
    Write-Host "Creating scanx-test-postgres container..."
    docker run --name scanx-test-postgres `
        -e POSTGRES_USER=test_user `
        -e POSTGRES_PASSWORD=test_password `
        -e POSTGRES_DB=test_db `
        -p 5432:5432 `
        -d postgres:15 | Out-Null
}

Write-Host "Waiting for PostgreSQL to be ready..."
for ($attempt = 1; $attempt -le 12; $attempt++) {
    docker exec scanx-test-postgres pg_isready -U test_user -d test_db | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "PostgreSQL is ready."
        break
    }

    if ($attempt -eq 12) {
        Write-Host "PostgreSQL did not become ready in time." -ForegroundColor Red
        exit 1
    }

    Start-Sleep -Seconds 2
}

# 6. Set test environment variables
$env:APP_ENV = "test"
$env:DEBUG = "false"
$env:DATABASE_URL = "postgresql+psycopg://test_user:test_password@localhost:5432/test_db"
$env:JWT_SECRET_KEY = $TestJwtSecret
$env:CORS_ALLOWED_ORIGINS = "http://localhost:5173,http://localhost:3000"

# 7. Formatting and linting
Run-Step "Ruff format" {
    ruff format .
}

Run-Step "Ruff auto-fix" {
    ruff check . --fix
}

Run-Step "Ruff final check" {
    ruff check .
}

# 8. Migrations and tests
Run-Step "Alembic migrations" {
    alembic upgrade head
}

Run-Step "Pytest" {
    python -m pytest
}

# 9. Python security checks
Run-Step "pip-audit production requirements" {
    pip-audit -r requirements.txt
}

Run-Step "pip-audit full local environment" {
    pip-audit
}

Run-Step "Bandit security scan" {
    bandit -r app -lll -iii
}

Run-Step "Semgrep security scan" {
    semgrep scan --config=p/python --config=p/owasp-top-ten --error
}

# 10. Gitleaks check
Run-Step "Gitleaks secret scan" {
    gitleaks detect --source . --redact
}

# 11. Trivy filesystem scan
Run-Step "Trivy filesystem vulnerability scan" {
    trivy fs --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 .
}

# 12. Docker build
Run-Step "Docker build" {
    docker build -t scanx-command-center-api:local .
}

# 13. Docker image scan
Run-Step "Trivy Docker image vulnerability scan" {
    trivy image --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 scanx-command-center-api:local
}

# 14. Optional Docker health check
Write-Host ""
Write-Host "Starting Docker container for /health check..."

$existingLocalContainer = docker ps -a --format "{{.Names}}" | Select-String -Pattern "^scanx-local-health-check$"

if ($existingLocalContainer) {
    docker rm -f scanx-local-health-check | Out-Null
}

docker run -d --name scanx-local-health-check `
    -p 8080:8080 `
    -e APP_ENV=local `
    -e DEBUG=false `
    -e APP_NAME=scanx-command-center-api-local `
    -e DATABASE_URL="postgresql+psycopg://test_user:test_password@host.docker.internal:5432/test_db" `
    -e JWT_SECRET_KEY="$TestJwtSecret" `
    -e CORS_ALLOWED_ORIGINS="http://localhost:5173,http://localhost:3000" `
    scanx-command-center-api:local | Out-Null

Write-Host "Waiting for local Docker app to start..."
Start-Sleep -Seconds 8

Run-Step "Docker container /health smoke test" {
    $response = Invoke-WebRequest -Uri "http://localhost:8080/health" -UseBasicParsing
    if ($response.StatusCode -ne 200) {
        throw "Health check failed with status code $($response.StatusCode)"
    }
    Write-Host $response.Content
}

Write-Host "Stopping local health check container..."
docker rm -f scanx-local-health-check | Out-Null

Write-Host ""
Write-Host "========================================"
Write-Host " ALL LOCAL CHECKS PASSED SUCCESSFULLY"
Write-Host " Safe to push to GitHub dev branch"
Write-Host "========================================"
Write-Host ""
