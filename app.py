import json
import os
import re
import subprocess
from typing import Dict, List, Optional

import requests
from flask import Flask, jsonify, redirect, render_template, request, url_for

app = Flask(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def _default_config() -> Dict:
    return {
        "radarr_url": (os.environ.get("RADARR_URL") or "").rstrip("/"),
        "radarr_api_key": os.environ.get("RADARR_API_KEY") or "",
        "file_paths": [],
    }


_config_cache: Optional[Dict] = None

_movies_cache: Optional[List[Dict]] = None


def load_config() -> Dict:
    """Load configuration from disk or environment defaults."""

    global _config_cache
    if _config_cache is not None:
        return _config_cache

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
            if not isinstance(data, dict):
                raise ValueError("Invalid configuration format")
            # Ensure expected keys are present.
            config = _default_config()
            config.update(data)
    except FileNotFoundError:
        config = _default_config()
    except Exception as exc:  # pragma: no cover - configuration file errors
        print(f"Failed to load configuration: {exc}")
        config = _default_config()

    config["radarr_url"] = (config.get("radarr_url") or "").strip().rstrip("/")
    config["radarr_api_key"] = (config.get("radarr_api_key") or "").strip()
    file_paths = config.get("file_paths", [])
    if not isinstance(file_paths, list):
        file_paths = [str(file_paths)] if file_paths else []
    config["file_paths"] = [os.path.abspath(os.path.expanduser(str(path))) for path in file_paths]

    _config_cache = config
    return config


def save_config(config: Dict) -> None:
    """Persist configuration to disk and reset caches."""

    global _config_cache, _movies_cache
    os.makedirs(os.path.dirname(CONFIG_PATH) or ".", exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
    _config_cache = config
    _movies_cache = None


def is_configured(config: Optional[Dict] = None) -> bool:
    """Return True when the application has been configured."""

    cfg = config or load_config()
    return bool(cfg.get("radarr_url") and cfg.get("radarr_api_key") and cfg.get("file_paths"))


def normalize_paths(raw_paths: str) -> List[str]:
    """Convert newline-separated paths into cleaned absolute paths."""

    paths: List[str] = []
    for line in raw_paths.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        expanded = os.path.abspath(os.path.expanduser(cleaned))
        if expanded not in paths:
            paths.append(expanded)
    return paths


@app.before_request
def ensure_configured() -> Optional[object]:
    """Redirect to the setup flow if the app has not been configured yet."""

    if request.endpoint in {"static", "setup"}:
        return None
    if request.endpoint is None:
        return None
    if is_configured():
        return None
    return redirect(url_for("setup"))


def get_all_movies() -> List[Dict]:
    """Fetch all movies from Radarr and cache the results."""
    global _movies_cache
    if _movies_cache is not None:
        return _movies_cache

    config = load_config()
    if not is_configured(config):
        return []

    try:
        response = requests.get(
            f"{config['radarr_url']}/api/v3/movie",
            headers={"X-Api-Key": config["radarr_api_key"]},
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
    config = load_config()
    return render_template("index.html", movies=movies, configured=is_configured(config))


@app.route("/create", methods=["POST"])
def create():
    config = load_config()
    if not is_configured(config):
        return jsonify({"logs": ["ERROR: Application has not been configured yet."]}), 503

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
            f"{config['radarr_url']}/api/v3/movie/{movie_id}",
            headers={"X-Api-Key": config["radarr_api_key"]},
            timeout=10,
        )
        response.raise_for_status()
        movie = response.json()
    except Exception as exc:  # pragma: no cover - network errors
        error(f"Could not retrieve movie info from Radarr (ID {movie_id}): {exc}")
        return jsonify({"logs": logs}), 502

    movie_path = movie.get("path")
    resolved_path = resolve_movie_path(movie_path, config)
    if resolved_path is None:
        error(f"Movie folder not found on disk: {movie_path}")
        return jsonify({"logs": logs}), 400

    movie_path = resolved_path
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


def resolve_movie_path(original_path: Optional[str], config: Dict) -> Optional[str]:
    """Resolve a movie folder path using configured library paths."""

    if original_path and os.path.isdir(original_path):
        return original_path

    if not original_path:
        return None

    folder_name = os.path.basename(original_path.rstrip(os.sep))
    if not folder_name:
        return None

    for base_path in config.get("file_paths", []):
        candidate = os.path.join(base_path, folder_name)
        if os.path.isdir(candidate):
            return candidate
    return None


@app.route("/setup", methods=["GET", "POST"])
def setup():
    config = load_config().copy()
    errors: List[str] = []

    if request.method == "POST":
        radarr_url = (request.form.get("radarr_url") or "").strip().rstrip("/")
        api_key = (request.form.get("radarr_api_key") or "").strip()
        raw_paths = request.form.get("file_paths") or ""
        file_paths = normalize_paths(raw_paths)

        if not radarr_url:
            errors.append("Radarr URL is required.")
        elif not re.match(r"^https?://", radarr_url):
            errors.append("Radarr URL must start with http:// or https://.")
        if not api_key:
            errors.append("Radarr API key is required.")
        if not file_paths:
            errors.append("At least one library path is required.")

        config.update(
            {
                "radarr_url": radarr_url,
                "radarr_api_key": api_key,
                "file_paths": file_paths,
            }
        )

        if not errors:
            save_config(config)
            return redirect(url_for("index"))

    return render_template("setup.html", config=config, errors=errors, configured=is_configured(config))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
