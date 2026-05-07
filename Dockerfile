# ⚡ Maxwell Protocol — Production Dockerfile
# Multi-stage build for minimal image size + non-root security

FROM python:3.11-slim AS builder

WORKDIR /build

COPY pyproject.toml .
RUN pip install --no-cache-dir --prefix=/install \
    "setuptools>=68.0" \
    "rich>=13.0.0" \
    "typer>=0.9.0" \
    "bitarray>=2.8.0" \
    "mmh3>=4.0.0" \
    "numpy>=1.24.0" \
    "aiohttp>=3.9.0" \
    "fastapi>=0.100.0" \
    "uvicorn>=0.23.0" \
    "httpx>=0.25.0" \
    "web3>=6.0.0" \
    "eth-account>=0.11.0"

COPY maxwell/ maxwell/
COPY rules.json .
RUN pip install --no-cache-dir --prefix=/install .

# ── Production image ───────────────────────────────────────────────
FROM python:3.11-slim

# Non-root user for security
RUN groupadd -r maxwell && useradd -r -g maxwell -d /app -s /sbin/nologin maxwell

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local
COPY --from=builder /build/rules.json .

# Create log directory owned by maxwell user
RUN mkdir -p /app/logs && chown -R maxwell:maxwell /app

USER maxwell

ENV PYTHONUNBUFFERED=1
ENV COLORTERM=truecolor

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')" || exit 1

CMD ["maxwell", "--mode", "server", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
