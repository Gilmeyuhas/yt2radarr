# 🎬 yt2radarr

A web-based UI to import YouTube videos directly into your Radarr-managed movie library.

Paste a YouTube URL, select a movie from your Radarr library, and this app will:
- Download the video using `yt-dlp`
- Rename it according to Plex/Radarr naming conventions
- Place it in the correct folder (or `Extras`/`Trailers` subfolder)

## ✨ Features
- ✅ Movie dropdown powered by Radarr API
- ✅ Strict YouTube-only URL input with validation
- ✅ Support for main movie files **or** extras
  
## 🧱 Requirements
- Python 3.11+
- `yt-dlp` and `ffmpeg` installed in your container or host
- A running Radarr instance (API key + base URL)
- Plex/Jellyfin to pick up the video after placement

## 🚀 Deployment
See `Dockerfile` to run in your Docker environment. Mount your Radarr movie library and set the `RADARR_API_KEY` and `RADARR_URL` as environment variables.

## 🔐 Security
- Input sanitized to prevent path traversal
- YouTube URLs enforced by regex and domain whitelist
- No secrets stored in the codebase
- Runs as non-root inside the container

---

Built for local use. LAN-only recommended. No authentication layer included (yet).
