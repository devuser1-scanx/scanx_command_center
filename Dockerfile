FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

RUN addgroup --system scanx && adduser --system --ingroup scanx scanx

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

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
