# --- Build stage ---
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

# Install dependencies first (cached layer)
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-install-project --no-dev || uv sync --no-install-project --no-dev

# Install the project itself
COPY src ./src
RUN uv sync --frozen --no-dev || uv sync --no-dev

# --- Runtime stage ---
FROM python:3.12-slim-bookworm

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

# Install system runtime libraries required by OpenCV and ONNX Runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the virtual environment from the builder
COPY --from=builder /app/.venv /app/.venv
COPY src ./src
COPY fixtures ./fixtures
COPY eval ./eval

ENV PATH="/app/.venv/bin:$PATH"

# Pre-download RapidOCR (PP-OCRv5) ONNX weights at build time into /root/.rapidocr so runtime needs no network
RUN python -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR()"

EXPOSE 8000

CMD ["uvicorn", "docvalidator.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
