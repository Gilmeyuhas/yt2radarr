FROM python:3.11-slim

# Install system packages needed by yt-dlp and for merging video/audio
# - ffmpeg: required for yt-dlp to merge streams and remux
# - curl/ca-certificates: good hygiene for TLS and debugging
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy and install Python deps first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# In case yt-dlp isn't already in requirements.txt, make sure it's installed
RUN pip install --no-cache-dir yt-dlp gunicorn

# Copy application code
COPY . .

# We will store runtime config (config.json, jobs.json) in /config,
# which will be a writable mounted volume in k8s.
# Make sure it exists with correct permissions.
RUN mkdir -p /config

# Create a non-root user and give it ownership of /app and /config
RUN useradd -m appuser \
    && chown -R appuser /app /config

USER appuser

# Environment variables
ENV FLASK_APP=app.py
ENV FLASK_ENV=production
# Tell the app (your new code) to use /config as its persistent storage
ENV YT2RADARR_CONFIG_DIR=/config

# Expose the port the app listens on
EXPOSE 5000

# Run the app with gunicorn instead of flask dev server
# - 0.0.0.0 so it listens inside the pod network
# - 1 worker is fine for now; can scale with replicas in k8s
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]

