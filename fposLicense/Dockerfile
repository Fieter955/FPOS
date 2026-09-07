# syntax=docker/dockerfile:1.7
FROM python:3.12.6-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY requirements.txt .
RUN python -m pip wheel --wheel-dir=/wheels -r requirements.txt

FROM python:3.12.6-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FPOS_LICENSE_DATA_DIR=/data \
    PORT=8080

RUN groupadd --gid 10001 fpos \
    && useradd --uid 10001 --gid fpos --no-log-init --create-home fpos \
    && mkdir -p /app /data \
    && chown -R fpos:fpos /app /data

WORKDIR /app
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels
COPY --chown=fpos:fpos app ./app
COPY --chown=fpos:fpos alembic ./alembic
COPY --chown=fpos:fpos alembic.ini .
COPY --chown=root:root docker-entrypoint.py /usr/local/bin/fpos-license-entrypoint

USER root
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/health',timeout=2)" || exit 1
ENTRYPOINT ["python", "/usr/local/bin/fpos-license-entrypoint"]
