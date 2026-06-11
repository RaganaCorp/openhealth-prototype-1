FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user to run the app.
RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Own the app dir and the data mount point so the non-root user can write the
# patient store, vector DB, and config. (--host 0.0.0.0 is container-internal;
# the host port is bound to 127.0.0.1 in the compose files.)
# Note: the compose files bind-mount ./data over /data. On Docker Desktop
# (mac/Windows — this project's targets) the file-sharing layer makes that
# writable for uid 1000; on a native Linux host the host ./data directory must
# be writable by uid 1000 (e.g. `chown -R 1000 ./data`).
RUN mkdir -p /data && chown -R appuser:appuser /app /data

USER appuser

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
