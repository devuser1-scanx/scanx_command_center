FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

# python:3.11-slim's Debian package snapshot lags behind upstream security
# fixes (e.g. util-linux CVEs) - pulling the latest patches here is what the
# Trivy scan in CI expects, since it flags known-fixed CVEs still present in
# the base image's installed versions.
RUN apt-get update && \
    apt-get upgrade -y && \
    rm -rf /var/lib/apt/lists/*

RUN addgroup --system scanx && adduser --system --ingroup scanx scanx

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade \
        pip \
        "setuptools>=78.1.1" \
        wheel && \
    python -m pip install --no-cache-dir --upgrade --force-reinstall \
        -r requirements.txt && \
    python -m pip check

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app
COPY assets ./assets

USER scanx

EXPOSE 8080

CMD gunicorn app.main:app \
    --bind 0.0.0.0:${PORT:-8080} \
    --workers 2 \
    --worker-class uvicorn.workers.UvicornWorker \
    --timeout 120
