FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    gosu \
    curl \
    unzip \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

ENV DENO_INSTALL=/usr/local/deno
ENV PATH="${DENO_INSTALL}/bin:${PATH}"

RUN curl -fsSL https://deno.land/install.sh | sh

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

ENV FLASK_APP=app.py
ENV FLASK_ENV=production
ENV YT2RADARR_CONFIG_DIR=/config

EXPOSE 5000

COPY ./entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

CMD ["gunicorn", "--no-control-socket", "--bind", "0.0.0.0:5000", "app:app"]
