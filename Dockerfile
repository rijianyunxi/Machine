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
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

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
