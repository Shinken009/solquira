FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY artifacts/solquira/backend/requirements.lock.txt /tmp/requirements.lock.txt
RUN pip install --no-cache-dir -r /tmp/requirements.lock.txt \
    && useradd --uid 10001 --create-home --user-group solquira \
    && mkdir /data && chown solquira:solquira /data
# Only use the allowlisted context made by stage-hosted-release.py.
COPY artifacts/ /app/artifacts/
COPY prototypes/ /app/prototypes/
COPY scripts/run-studio-hosted.py /app/scripts/run-studio-hosted.py
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/solquira-api/healthz',timeout=3)"
CMD ["python", "scripts/run-studio-hosted.py"]
