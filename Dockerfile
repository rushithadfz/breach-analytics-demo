# Single container: FastAPI serves the API, the built frontend, and the
# evidence documents from one origin.
#
# One service rather than two is the whole point. Free tiers give you one
# always-on instance if you are lucky; splitting the frontend onto a
# static host and the API onto a second free service means two cold
# starts, a CORS configuration, and two things that can be asleep when
# somebody opens the link.

# --- build the frontend ------------------------------------------------
FROM node:20-slim AS frontend
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
# Empty base URL => the browser calls the same origin it was served from,
# so no build-time knowledge of the deployment hostname is needed.
ENV VITE_API_BASE_URL=/api/v1
RUN npm run build

# --- runtime -----------------------------------------------------------
FROM python:3.12-slim

# tesseract is a real dependency, not a convenience: scanned documents
# are ~40% of the corpus and quarantine without it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY --from=frontend /app/dist ./frontend-dist/

# The compressed corpus (43 MB) rather than the original (191 MB). It
# backs the evidence viewer only; accuracy figures come from the full
# corpus locally — see compress_corpus.py.
COPY corpus-deploy/ ./corpus/

# A pre-built database ships with the image so the container starts
# ready. Rebuilding it here would mean running OCR over 450 pages at
# image-build time, which no free tier will sit through.
COPY backend/breach_analytics.db ./backend/breach_analytics.db

ENV PYTHONUNBUFFERED=1 \
    FRONTEND_DIST=/srv/frontend-dist \
    CORPUS_DIR=/srv/corpus \
    DATABASE_URL=sqlite:////srv/backend/breach_analytics.db

WORKDIR /srv/backend

# 7860 is the port Hugging Face Spaces expects; harmless elsewhere and
# overridable with $PORT for hosts that assign one.
ENV PORT=7860
EXPOSE 7860

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
