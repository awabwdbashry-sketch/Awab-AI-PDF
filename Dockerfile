FROM python:3.11-slim

# Prevent .pyc files and enable unbuffered stdout/stderr so logs show up
# immediately in Railway's log viewer.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# build-essential is required to build some ML deps (faiss/sentence-transformers
# dependency chain) on slim images.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (separate layer so code-only changes
# don't force a full dependency reinstall on every deploy).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project.
COPY . .

# Make sure the runtime data folders exist even on a completely fresh
# checkout (they're also created defensively at import time by the app
# itself, this is just belt-and-suspenders for the container build).
RUN mkdir -p app/uploads/texts app/uploads/vectors app/uploads/exports app/database

# Railway provides $PORT at runtime; 8000 is just a sane local default.
ENV PORT=8000
EXPOSE 8000

# Shell form so ${PORT} is expanded by the shell at container start -
# Railway sets $PORT dynamically and it is NOT known at build time.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
