FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends librdkafka-dev gcc && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY fleetpulse ./fleetpulse
COPY web ./web
COPY db ./db
RUN useradd -r -u 10001 app && chown -R app /app
USER 10001
# one image, three roles: api | ingest | simulator (command set by compose / Helm)
CMD ["uvicorn", "fleetpulse.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
