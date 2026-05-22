FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
RUN pip install .

COPY app ./app

EXPOSE 8000

# --forwarded-allow-ips="*" trusts X-Forwarded-Proto / X-Forwarded-For from
# Railway's edge proxy (uvicorn's default trusts only 127.0.0.1, which strips
# the headers on a PaaS where the proxy isn't on localhost). Without this the
# app sees scheme=http and builds redirects with http:// — the MCP Streamable
# HTTP transport's trailing-slash redirect (/mcp -> /mcp/) gets downgraded and
# OAuth clients refuse to follow it. Safe on Railway: the container is only
# reachable via Railway's edge, so external clients can't spoof the headers.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --forwarded-allow-ips=*"]
