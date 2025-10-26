import os
import re
import subprocess
from typing import Dict, List, Optional

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

RADARR_URL = os.environ.get("RADARR_URL", "http://localhost:7878")
RADARR_API_KEY = os.environ.get("RADARR_API_KEY", "YOUR_RADARR_API_KEY")

_movies_cache: Optional[List[Dict]] = None


def get_all_movies() -> List[Dict]:
    """Fetch all movies from Radarr and cache the results."""
    global _movies_cache
    if _movies_cache is not None:
        return _movies_cache

    try:
        response = requests.get(
            f"{RADARR_URL}/api/v3/movie",
            headers={"X-Api-Key": RADARR_API_KEY},
            timeout=10,
        )
        response.raise_for_status()
        movies = response.json()
        movies.sort(key=lambda movie: movie.get("title", "").lower())
        _movies_cache = movies
        return movies
    except Exception as exc:  # pragma: no cover - network errors
        print(f"Error fetching movies from Radarr: {exc}")
        return []


def sanitize_filename(name: str) -> str:
    """Sanitize a string to be safe as a filename."""
    sanitized = re.sub(r'[\\/:*?"<>|]+', "_", name)
    return sanitized.strip().rstrip('.')


def build_format_selector(resolution: str) -> str:
    """Return a yt-dlp format selector for the requested resolution."""
    mapping = {
        "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        "720p": "bestvideo[height<=720]+bestaudio/best[height<=720]",
        "480p": "bestvideo[height<=480]+bestaudio/best[height<=480]",
        "best": "bestvideo+bestaudio/best",
    }
    return mapping.get(resolution, mapping["best"])


def resolve_movie_by_metadata(
    movie_id: str,
    tmdb: str,
    title: str,
    year: str,
    log,
) -> Optional[Dict]:
    """Attempt to resolve a Radarr movie by assorted metadata."""
    if movie_id:
        return {"id": str(movie_id)}

    movies = get_all_movies()
    if tmdb:
        for movie in movies:
            if str(movie.get("tmdbId") or "") == tmdb:
                log(f"Matched TMDb ID {tmdb} to Radarr movie '{movie.get('title')}'.")
                return movie
    if title:
        lowered = title.lower()
        matches = [movie for movie in movies if movie.get("title", "").lower() == lowered]
        if year:
            matches = [movie for movie in matches if str(movie.get("year") or "") == year]
        if matches:
            match = matches[0]
            log(
                f"Matched title '{title}'{' (' + year + ')' if year else ''} to Radarr movie '{match.get('title')}'."
            )
            return match
    return None


@app.route("/", methods=["GET"])
def index():
    movies = get_all_movies()
    return render_template("index.html", movies=movies)


@app.route("/create", methods=["POST"])
def create():
    data = request.get_json(silent=True) or {}
    logs: List[str] = []
    errors: List[str] = []

    def log(message: str) -> None:
        logs.append(message)

    def error(message: str) -> None:
        logs.append(f"ERROR: {message}")
        errors.append(message)

    yt_url = (data.get("yturl") or "").strip()
    if not yt_url:
        error("YouTube URL is required.")
    elif not re.search(r"(youtube\\.com|youtu\\.be)/", yt_url):
        error("Please provide a valid YouTube URL.")

    movie_id = (data.get("movieId") or "").strip()
    tmdb = (data.get("tmdb") or "").strip()
    title = (data.get("title") or "").strip()
    year = (data.get("year") or "").strip()

    resolved = resolve_movie_by_metadata(movie_id, tmdb, title, year, log)
    if resolved is None or not str(resolved.get("id")):
        error("No movie selected. Please choose a movie from the suggestions list.")
        return jsonify({"logs": logs}), 400
    movie_id = str(resolved.get("id"))

    extra_type = (data.get("extraType") or "trailer").strip().lower()
    allowed_extra_types = {
        "trailer",
        "behindthescenes",
        "deleted",
        "featurette",
        "interview",
        "scene",
        "short",
        "other",
    }
    if extra_type not in allowed_extra_types:
        log(f"Unknown extra type '{extra_type}', defaulting to 'other'.")
        extra_type = "other"

    extra = bool(data.get("extra"))
    extra_name = (data.get("extra_name") or "").strip()
    resolution = (data.get("resolution") or "best").strip().lower()
    extension = (data.get("extension") or "mp4").strip().lower()

    if extra and not extra_name:
        error("Extra name is required when storing in a subfolder.")
    if extension not in {"mp4", "mkv"}:
        error(f"Unsupported file extension '{extension}'.")

    if errors:
        return jsonify({"logs": logs}), 400

    try:
        log(f"Fetching Radarr details for movie ID {movie_id}.")
        response = requests.get(
            f"{RADARR_URL}/api/v3/movie/{movie_id}",
            headers={"X-Api-Key": RADARR_API_KEY},
            timeout=10,
        )
        response.raise_for_status()
        movie = response.json()
    except Exception as exc:  # pragma: no cover - network errors
        error(f"Could not retrieve movie info from Radarr (ID {movie_id}): {exc}")
        return jsonify({"logs": logs}), 502

    movie_path = movie.get("path")
    if not movie_path or not os.path.isdir(movie_path):
        error(f"Movie folder not found on disk: {movie_path}")
        return jsonify({"logs": logs}), 400

    log(f"Movie path resolved to '{movie_path}'.")

    folder_map = {
        "trailer": "Trailers",
        "behindthescenes": "Behind The Scenes",
        "deleted": "Deleted Scenes",
        "featurette": "Featurettes",
        "interview": "Interviews",
        "scene": "Scenes",
        "short": "Shorts",
        "other": "Other",
    }

    target_dir = movie_path
    if extra:
        subfolder = folder_map.get(extra_type, extra_type.capitalize() + "s")
        target_dir = os.path.join(movie_path, subfolder)
        os.makedirs(target_dir, exist_ok=True)
        log(f"Storing video in subfolder '{subfolder}'.")
    else:
        log("Storing video alongside primary movie files.")

    descriptive = extra_name
    if descriptive:
        log(f"Using custom descriptive name '{descriptive}'.")
    else:
        try:
            log("Querying yt-dlp for video title.")
            proc = subprocess.run(
                ["yt-dlp", "--get-title", yt_url],
                capture_output=True,
                text=True,
                check=True,
            )
            descriptive = proc.stdout.strip() or "Video"
            log(f"Using YouTube title '{descriptive}'.")
        except Exception as exc:  # pragma: no cover - command failure
            descriptive = "Video"
            logs.append(
                f"WARNING: Failed to retrieve title from yt-dlp ({exc}). Using fallback name 'Video'."
            )

    descriptive = sanitize_filename(descriptive)

    if extra:
        filename = f"{descriptive}.{extension}"
    else:
        filename = f"{descriptive}-{extra_type}.{extension}"

    target_path = os.path.join(target_dir, filename)
    if os.path.exists(target_path):
        base_name, ext_part = os.path.splitext(filename)
        log(f"File '{filename}' already exists. Searching for a free filename.")
        index = 1
        while True:
            if extra:
                new_filename = f"{base_name} ({index}){ext_part}"
            else:
                if base_name.endswith(f"-{extra_type}"):
                    base_descr = base_name[: -len(f"-{extra_type}")]
                else:
                    base_descr = base_name
                new_filename = f"{base_descr} ({index})-{extra_type}{ext_part}"
            candidate = os.path.join(target_dir, new_filename)
            if not os.path.exists(candidate):
                target_path = candidate
                log(f"Selected new filename '{new_filename}'.")
                break
            index += 1

    format_selector = build_format_selector(resolution)
    command = [
        "yt-dlp",
        "-f",
        format_selector,
        "--merge-output-format",
        extension,
        "-o",
        target_path,
        yt_url,
    ]

    log(f"Running yt-dlp with format '{format_selector}'.")
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except Exception as exc:  # pragma: no cover - command failure
        error(f"Failed to invoke yt-dlp: {exc}")
        return jsonify({"logs": logs}), 500

    if result.returncode != 0:
        error_output = (result.stderr or result.stdout or "").strip()
        error(f"Download failed: {error_output[:300]}")
        return jsonify({"logs": logs}), 500

    if result.stdout:
        for line in result.stdout.strip().splitlines():
            log(line)
    if result.stderr:
        for line in result.stderr.strip().splitlines():
            logs.append(f"WARNING: {line}")

    log(f"Success! Video downloaded to '{target_path}'.")
    return jsonify({"logs": logs}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
