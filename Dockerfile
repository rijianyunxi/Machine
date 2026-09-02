# syntax=docker/dockerfile:1

# Build the React/Vite panel first.
FROM node:22-bookworm-slim AS frontend-builder
WORKDIR /src/webapp/spa

COPY webapp/spa/package.json webapp/spa/package-lock.json ./
RUN npm ci

COPY webapp/spa/ ./
RUN npm run build

# Python runtime for the detection service and API panel.
FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Runtime libraries used by OpenCV, FFmpeg/RTSP and PyTorch.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./

# Keep the PyTorch pair explicit: an unconstrained torchvision dependency can
# resolve to a CUDA-enabled torch wheel on Linux and add several gigabytes.
ARG TORCH_VARIANT=cpu
ARG TORCH_VERSION=2.8.0
ARG TORCHVISION_VERSION=0.23.0
RUN set -eux; \
    python -m pip install --upgrade pip; \
    case "${TORCH_VARIANT}" in \
        cpu) \
            python -m pip install --no-cache-dir \
                --index-url https://pypi.org/simple \
                --extra-index-url https://download.pytorch.org/whl/cpu \
                "torch==${TORCH_VERSION}+cpu" \
                "torchvision==${TORCHVISION_VERSION}+cpu" \
            ;; \
        gpu) \
            python -m pip install --no-cache-dir \
                --index-url https://pypi.org/simple \
                "torch==${TORCH_VERSION}" \
                "torchvision==${TORCHVISION_VERSION}" \
            ;; \
        *) \
            echo "TORCH_VARIANT must be cpu or gpu, got: ${TORCH_VARIANT}" >&2; \
            exit 1 \
            ;; \
    esac; \
    python -m pip install --no-cache-dir \
        --upgrade-strategy only-if-needed \
        -r requirements.txt

# Copy the application source. Runtime data is persisted through /app/storage.
COPY . .
COPY --from=frontend-builder /src/webapp/spa/dist ./webapp/spa/dist

RUN mkdir -p /app/storage/models /app/storage/datasets \
                /app/storage/snapshots /app/storage/test_results \
                /app/storage/backups

COPY docker-entrypoint.sh /usr/local/bin/machine-entrypoint
RUN chmod +x /usr/local/bin/machine-entrypoint

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/machine-entrypoint"]
CMD ["python", "main.py"]
