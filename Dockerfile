FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for layer caching of other Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Force the newest yt-dlp and gunicorn every build
# -U makes sure yt-dlp is at latest (nightly-ish)
RUN pip install --no-cache-dir --upgrade yt-dlp gunicorn

# Copy the rest of the app
COPY . .

RUN mkdir -p /config

# Create the runtime user
RUN useradd -m appuser && chown -R appuser /app /config
USER appuser

ENV FLASK_APP=app.py
ENV FLASK_ENV=production
ENV YT2RADARR_CONFIG_DIR=/config

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]

