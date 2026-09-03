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
    HF_HOME=/models/huggingface \
    TRANSFORMERS_OFFLINE=1

RUN mkdir -p /models/huggingface && \
    python -m pip install --no-cache-dir \
      "pypdfium2>=4.30" \
      "pillow>=10.0" \
      "rapidocr-onnxruntime>=1.4" \
      "numpy>=1.26"

# Pre-download RapidOCR (PP-OCRv5) ONNX weights at build time so runtime needs no network.
RUN python -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR()"

WORKDIR /app

# Copy the virtual environment from the builder (self-contained, uv binary not needed at runtime)
COPY --from=builder /app/.venv /app/.venv
COPY src ./src
COPY fixtures ./fixtures
COPY eval ./eval

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "docvalidator.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
