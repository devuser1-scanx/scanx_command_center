FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

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

USER scanx

EXPOSE 8080

CMD gunicorn app.main:app \
    --bind 0.0.0.0:${PORT:-8080} \
    --workers 2 \
    --worker-class uvicorn.workers.UvicornWorker \
    --timeout 120
