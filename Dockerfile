# Stage 1: Build Frontend Assets
FROM node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS frontend-builder

WORKDIR /build/frontend

# Copy locked dependencies and source
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# Stage 2: Monolith Runtime
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Install minimal OS dependencies and astral uv
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    curl -LsSf https://astral.sh/uv/install.sh | sh && \
    mv /root/.local/bin/uv /usr/local/bin/uv && \
    rm -rf /var/lib/apt/lists/*

# Copy dependency specifications and install with frozen lockfile
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application source code, migrations, and fixtures
COPY src/ ./src/
COPY sql/ ./sql/
COPY fixtures/ ./fixtures/

# Copy built frontend assets from builder stage
COPY --from=frontend-builder /build/frontend/dist ./frontend/dist

# Install moneytrail package into the virtual environment
RUN uv pip install --no-deps -e .

# Create non-root application user and secure data directory
RUN useradd -m -u 1000 -s /bin/bash moneytrail && \
    mkdir -p /app/data && \
    chown -R moneytrail:moneytrail /app

USER moneytrail

EXPOSE 8000

# Run FastAPI monolith binding loopback
CMD ["uvicorn", "moneytrail.api:app", "--host", "127.0.0.1", "--port", "8000"]
