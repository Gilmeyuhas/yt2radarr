# 🎬 yt2radarr

A web UI that turns any publicly accessible YouTube video into a Radarr-ready movie file. Paste a YouTube URL, pick a movie from Radarr, and yt2radarr will download, rename, and drop the file exactly where Radarr (and Plex/Jellyfin) expects it.

> Built purely as a hobby project by [Gil Meyuhas](https://github.com/Gilmeyuhas) to make managing a personal media library easier. If you want to help improve it, please open an issue or pull request. Community contributions are more than welcome!

![Screenshot of the YT2Radarr dashboard](static/yt2radarr-dashboard.png)

## ✨ What it does

* Fetches your entire Radarr library so you can attach a download to the exact title (including extras such as trailers or behind-the-scenes clips).
* Uses `yt-dlp` with a tuned format selector to prefer high bitrate HLS/H.264 sources before falling back to other codecs.
* Renames downloads to Plex/Radarr naming conventions and resolves extras into sub-folders when requested.
* Applies optional Radarr path overrides so the importer works in Docker, Kubernetes, or directly on your workstation.
* Records download jobs and progress so you can review historical runs.

### Real-world example
I use yt2radarr to keep live concerts and documentaries in my home media library. Paste the YouTube link, choose the matching movie or an "Extras" subfolder, and Plex picks it up instantly without any manual file management.

## 🧰 Requirements

* Python 3.11+
* `ffmpeg` and `yt-dlp`
* A running Radarr instance (API key + base URL)
* Access to the Radarr-managed movie folders (directly or via mounted volumes)

## 🚀 Getting started

### 1. Clone & install
```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt yt-dlp
```

### 2. Run locally
```bash
export FLASK_APP=app.py
export YT2RADARR_CONFIG_DIR=$(pwd)/.config   # Optional but keeps config out of the repo
mkdir -p "$YT2RADARR_CONFIG_DIR"
flask run --host 0.0.0.0 --port 5000
```
The first visit to <http://localhost:5000> redirects you to the **Setup** page where you’ll enter your Radarr details, movie library paths, path overrides, and optional cookies.

### 3. Docker or Compose
The included `Dockerfile` runs the app with Gunicorn and stores configuration in `/config` so you can mount a persistent volume. A generic `docker-compose.yml` looks like this:

```yaml
services:
  yt2radarr:
    build: .
    container_name: yt2radarr
    restart: unless-stopped
    ports:
      - "${YT2RADARR_PORT:-5010}:5000"
    environment:
      RADARR_URL: ${RADARR_URL:-http://radarr:7878}
      RADARR_API_KEY: ${RADARR_API_KEY}
      YT2RADARR_CONFIG_DIR: /config
      # Optional: uncomment to point at an existing cookies file
      # YT_COOKIE_FILE: /config/cookies.txt
    volumes:
      - ./config:/config
      - ${MOVIES_PATH:-/path/to/movies}:/movies
      # Optional cookies mount (commented out by default)
      # - ${COOKIE_FILE_PATH:-./cookies/cookies.txt}:/config/cookies.txt:ro
```

> [!WARNING]
> Do not commit config.json, jobs.json, or cookies.txt. These can contain credentials.

Bring it up with:
```bash
docker compose up --build
```

Everything about the Compose file is customizable - swap ports, change mount points, or reference secrets managers for credentials. As long as Radarr and yt2radarr can see the same movie directories (directly or via overrides), the app should behave the same way on any machine.

## ⚙️ Configuration reference

| Setting | Description |
| --- | --- |
| **Radarr URL** | Base URL of your Radarr instance (e.g. `http://<yourip>:7878`). |
| **Radarr API Key** | Generate it under Radarr ➝ Settings ➝ General. |
| **Movie Library Paths** | Absolute paths available to yt2radarr. Used to locate folders and avoid duplicates. |
| **Path Overrides** | Map Radarr’s internal paths to the paths available on this host/container. Format: `remote => local`. |
| **Debug Mode** | When enabled, shows the full log in the UI console. |
| **YouTube Cookies (optional)** | Paste a Netscape-format cookies file to bypass any authentication problems. Saved as `cookies.txt` with owner-only permissions in your config directory. |

### Working with cookies
1. Export cookies with your browser or let `yt-dlp` do it for you:
   ```bash
   yt-dlp --cookies-from-browser chrome --cookies cookies.txt "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ"
   ```
2. Open the **Settings → YouTube Cookies** section and paste the contents of `cookies.txt`. yt2radarr stores it under your configuration directory and immediately locks the file down to owner read/write (0600 on Unix-like systems) so it isn’t exposed to other users on the host.
3. Prefer to manage secrets outside the app? Mount a file and point `YT_COOKIE_FILE` at it—the environment variable wins over anything saved via the UI, which keeps existing setups working without changes. A read-only bind mount or secret store integration works well here. I myself store the cookies in a Kubernetes secret.
4. Need to rotate cookies? Paste the fresh file or tick “Remove saved cookies” to delete the stored copy. The UI never redisplays saved cookies; it only acknowledges whether a file exists.

## 🛠 Tips for portability

* **Config directory**: Override `YT2RADARR_CONFIG_DIR` to pick where `config.json`, `jobs.json`, and `cookies.txt` live. Mount it as a volume in containers to keep settings between restarts.
* **Permissions**: Run the container as any user; just ensure it has read/write access to the config directory and Radarr library mounts.
* **Network access**: yt2radarr only needs outbound access to Radarr and YouTube/CDN endpoints.
* **Radarr path differences**: Use the Path Overrides section if Radarr is in a different container/pod than yt2radarr. This keeps downloads portable between macOS, Linux, Windows, and NAS setups.

## 🤝 Contributing
This project exists to scratch my own itch, but it’s open-source because others might find it useful too. Issues, feature ideas, documentation tweaks, puns, and pull requests are all encouraged. If you build something neat, please share it!

## 📄 License
MIT
