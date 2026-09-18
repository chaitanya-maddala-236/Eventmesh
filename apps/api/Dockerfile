FROM python:3.12-slim AS base
WORKDIR /srv
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev curl && rm -rf /var/lib/apt/lists/*

COPY apps/api/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

COPY packages /srv/packages
RUN pip install --no-cache-dir \
    -e /srv/packages/common \
    -e /srv/packages/config \
    -e /srv/packages/database \
    -e /srv/packages/messaging \
    -e /srv/packages/auth \
    -e /srv/packages/logging \
    -e /srv/packages/metrics

COPY apps/api /srv/app
RUN pip install --no-cache-dir -e /srv/app

RUN useradd --create-home --uid 1000 eventmesh && chown -R eventmesh:eventmesh /srv
USER eventmesh

EXPOSE 8000
CMD ["uvicorn", "eventmesh_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
