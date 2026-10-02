ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_NO_CACHE=1
WORKDIR /app
# dependencies first: this layer is cached until uv.lock changes
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:${PYTHON_VERSION}-slim AS runtime
LABEL org.opencontainers.image.source="https://github.com/Cheburek28/PCBInspectionService" \
      org.opencontainers.image.description="PCB photo inspection against a reference, with operator feedback dataset" \
      org.opencontainers.image.licenses="Apache-2.0"
# security updates on top of the base image (it lags behind Debian fixes)
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --create-home app \
 && mkdir -p /data && chown app:app /data
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PCBIS_STORAGE_PATH=/data
USER app
VOLUME ["/data"]
EXPOSE 8000
ENTRYPOINT ["tini", "--", "entrypoint.sh"]
CMD ["api"]
