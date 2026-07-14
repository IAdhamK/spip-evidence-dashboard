FROM node:22-slim AS frontend-build

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci

COPY frontend/ ./
ARG VITE_API_BASE_URL=same-origin
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM python:3.12-slim

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-dejavu-core libreoffice-calc-nogui libreoffice-impress-nogui \
        libreoffice-writer-nogui poppler-utils \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-ind \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=frontend-build /frontend/dist ./static

ENV PYTHONPATH=/app
ENV STATIC_DIR=/app/static
ENV DATABASE_PATH=/app/data/evidence.db
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/ready', timeout=2).read()"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
