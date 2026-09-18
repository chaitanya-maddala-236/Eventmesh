FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONUNBUFFERED=1
COPY apps/demo-webhook/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt
COPY apps/demo-webhook /srv/app
WORKDIR /srv/app
RUN useradd --create-home --uid 1000 eventmesh && chown -R eventmesh:eventmesh /srv
USER eventmesh
EXPOSE 9000
CMD ["uvicorn", "demo_webhook.main:app", "--host", "0.0.0.0", "--port", "9000"]
