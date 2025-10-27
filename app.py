import json
import os
import re
import subprocess
import threading
import uuid
from typing import Dict, List, Optional, Tuple

import requests
from flask import Flask, jsonify, redirect, render_template, request, url_for

from jobs import JobRepository

app = Flask(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
JOBS_PATH = os.path.join(os.path.dirname(__file__), "jobs.json")


def chrome_cookie_candidates() -> List[str]:
    """Return potential Chrome cookie database locations for the current OS."""

    return [
        "~/.config/google-chrome/Default/Cookies",  # Linux containers / WSL
        "~/Library/Application Support/Google/Chrome/Default/Cookies",  # macOS
    ]


def chrome_cookie_path() -> Optional[str]:
    """Return the first Chrome cookie database path that exists."""

    for candidate in chrome_cookie_candidates():
        expanded = os.path.expanduser(candidate)
        if os.path.exists(expanded):
            return expanded
    return None




def _default_config() -> Dict:
    return {
        "radarr_url": (os.environ.get("RADARR_URL") or "").rstrip("/"),
        "radarr_api_key": os.environ.get("RADARR_API_KEY") or "",
        "file_paths": [],
        "path_overrides": [],
    }


_config_cache: Optional[Dict] = None

_movies_cache: Optional[List[Dict]] = None

jobs_repo = JobRepository(JOBS_PATH, max_items=50)


def append_job_log(job_id: str, message: str) -> None:
    jobs_repo.append_logs(job_id, [message])


def _mark_job_failure(job_id: str, message: str) -> None:
    jobs_repo.mark_failure(job_id, message)


def _mark_job_success(job_id: str) -> None:
    jobs_repo.mark_success(job_id)


def _job_status(job_id: str, status: str, progress: Optional[float] = None) -> None:
    jobs_repo.status(job_id, status, progress=progress)


def normalize_path_overrides(overrides: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Sanitize and de-duplicate path override entries."""

    normalized: List[Dict[str, str]] = []
    for entry in overrides:
        if not isinstance(entry, dict):
            continue
        remote = str(entry.get("remote") or "").strip()
        local = str(entry.get("local") or "").strip()
        if not remote or not local:
            continue
        remote_clean = remote.rstrip("/\\") or remote
        local_clean = os.path.abspath(os.path.expanduser(local))
        record = {"remote": remote_clean, "local": local_clean}
        if record not in normalized:
            normalized.append(record)
    return normalized


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

    overrides_raw = config.get("path_overrides", [])
    if not isinstance(overrides_raw, list):
        overrides_raw = []
    config["path_overrides"] = normalize_path_overrides(overrides_raw)

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


def parse_path_overrides(raw_overrides: str) -> Tuple[List[Dict[str, str]], List[str]]:
    """Parse override definitions of the form 'remote => local'."""

    overrides: List[Dict[str, str]] = []
    errors: List[str] = []
    for index, line in enumerate(raw_overrides.splitlines(), start=1):
        cleaned = line.strip()
        if not cleaned:
            continue
        separator: Optional[str] = None
        for candidate in ("=>", "->", ","):
            if candidate in cleaned:
                separator = candidate
                break
        if separator is None:
            errors.append(
                f"Path override line {index} must use 'remote => local' format: {cleaned!r}"
            )
            continue
        remote_raw, local_raw = cleaned.split(separator, 1)
        remote = remote_raw.strip()
        local = local_raw.strip()
        if not remote or not local:
            errors.append(
                f"Path override line {index} is missing a remote or local path: {cleaned!r}"
            )
            continue
        overrides.append({"remote": remote, "local": local})
    return overrides, errors


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


EXTRA_TYPE_LABELS = {
    "trailer": "Trailer",
    "behindthescenes": "Behind the Scenes",
    "deleted": "Deleted Scene",
    "featurette": "Featurette",
    "interview": "Interview",
    "scene": "Scene",
    "short": "Short",
    "other": "Other",
}


RESOLUTION_LABELS = {
    "best": "Best Available",
    "1080p": "Up to 1080p",
    "720p": "Up to 720p",
    "480p": "Up to 480p",
}


def _describe_job(payload: Dict) -> Dict:
    movie_label = (payload.get("movieName") or payload.get("title") or "").strip()
    if not movie_label:
        movie_label = "Selected Movie"
    extra = bool(payload.get("extra"))
    extra_type = (payload.get("extraType") or "trailer").strip().lower()
    extra_name = (payload.get("extra_name") or "").strip()
    extra_label = extra_name or EXTRA_TYPE_LABELS.get(extra_type, extra_type.capitalize())
    if extra and extra_label:
        label = f"{movie_label} – {extra_label}"
        subtitle = f"Extra • {extra_label}"
    else:
        label = movie_label
        subtitle = ""
    resolution = (payload.get("resolution") or "best").strip().lower()
    metadata = [
        "Stored as extra content" if extra else "",
        f"Format: {(payload.get('extension') or 'mp4').strip().upper() or 'MP4'}",
        f"Resolution: {RESOLUTION_LABELS.get(resolution, resolution or 'Best Available')}",
    ]
    metadata = [item for item in metadata if item]
    return {"label": label or "Radarr Download", "subtitle": subtitle, "metadata": metadata}


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

    def error(message: str) -> None:
        logs.append(f"ERROR: {message}")
        errors.append(message)

    yt_url = (data.get("yturl") or "").strip()
    if not yt_url:
        error("YouTube URL is required.")
    elif not re.search(r"(youtube\.com|youtu\.be)/", yt_url):
        error("Please provide a valid YouTube URL.")

    movie_id = (data.get("movieId") or "").strip()
    if not movie_id:
        error("No movie selected. Please choose a movie from the suggestions list.")

    extra = bool(data.get("extra"))
    extra_name = (data.get("extra_name") or "").strip()
    if extra and not extra_name:
        error("Extra name is required when storing in a subfolder.")

    extension = (data.get("extension") or "mp4").strip().lower()
    if extension not in {"mp4", "mkv"}:
        error(f"Unsupported file extension '{extension}'.")

    if errors:
        return jsonify({"logs": logs}), 400

    payload = {
        "yturl": yt_url,
        "movieId": movie_id,
        "movieName": (data.get("movieName") or "").strip(),
        "title": (data.get("title") or "").strip(),
        "year": (data.get("year") or "").strip(),
        "tmdb": (data.get("tmdb") or "").strip(),
        "resolution": (data.get("resolution") or "best").strip().lower(),
        "extension": extension,
        "extra": extra,
        "extraType": (data.get("extraType") or "trailer").strip().lower(),
        "extra_name": extra_name,
    }

    descriptors = _describe_job(payload)
    job_id = str(uuid.uuid4())
    job_record = jobs_repo.create(
        {
            "id": job_id,
            "status": "queued",
            "progress": 0,
            "label": descriptors["label"],
            "subtitle": descriptors["subtitle"],
            "metadata": descriptors["metadata"],
            "message": "",
            "logs": ["Job queued."],
            "request": payload,
        }
    )

    worker = threading.Thread(target=process_download_job, args=(job_id, payload), daemon=True)
    worker.start()

    return jsonify({"job": job_record}), 202


def process_download_job(job_id: str, payload: Dict) -> None:
    def log(message: str) -> None:
        append_job_log(job_id, message)

    def warn(message: str) -> None:
        append_job_log(job_id, f"WARNING: {message}")

    def fail(message: str) -> None:
        append_job_log(job_id, f"ERROR: {message}")
        _mark_job_failure(job_id, message)

    try:
        _job_status(job_id, "processing", progress=1)
        config = load_config()
        if not is_configured(config):
            fail("Application has not been configured yet.")
            return

        yt_url = (payload.get("yturl") or "").strip()
        movie_id = (payload.get("movieId") or "").strip()
        tmdb = (payload.get("tmdb") or "").strip()
        title = (payload.get("title") or "").strip()
        year = (payload.get("year") or "").strip()

        cookie_path = chrome_cookie_path()
        cookie_args = ["--cookies-from-browser", "chrome"] if cookie_path else []
        if cookie_path:
            log(f"Using Chrome cookies database at '{cookie_path}'.")
        else:
            checked = ", ".join(os.path.expanduser(path) for path in chrome_cookie_candidates())
            log(
                "Chrome cookies database not found; checked: "
                f"{checked or 'no known locations'}. Running yt-dlp without browser cookies."
            )

        resolved = resolve_movie_by_metadata(movie_id, tmdb, title, year, log)
        if resolved is None or not str(resolved.get("id")):
            fail("No movie selected. Please choose a movie from the suggestions list.")
            return
        movie_id = str(resolved.get("id"))

        extra_type = (payload.get("extraType") or "trailer").strip().lower()
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
        payload["extraType"] = extra_type

        descriptors = _describe_job(payload)
        jobs_repo.update(
            job_id,
            {
                "label": descriptors["label"],
                "subtitle": descriptors["subtitle"],
                "metadata": descriptors["metadata"],
                "request": payload,
            },
        )

        extra = bool(payload.get("extra"))
        extra_name = (payload.get("extra_name") or "").strip()
        resolution = (payload.get("resolution") or "best").strip().lower()
        extension = (payload.get("extension") or "mp4").strip().lower()

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
            fail(f"Could not retrieve movie info from Radarr (ID {movie_id}): {exc}")
            return

        movie_path = movie.get("path")
        resolved_path = resolve_movie_path(movie_path, config)
        if resolved_path is None:
            fail(f"Movie folder not found on disk: {movie_path}")
            return

        movie_path = resolved_path
        log(f"Movie path resolved to '{movie_path}'.")
        _job_status(job_id, "processing", progress=10)

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
                    ["yt-dlp", *cookie_args, "--get-title", yt_url],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                descriptive = proc.stdout.strip() or "Video"
                log(f"Using YouTube title '{descriptive}'.")
            except Exception as exc:  # pragma: no cover - command failure
                descriptive = "Video"
                warn(
                    f"Failed to retrieve title from yt-dlp ({exc}). Using fallback name 'Video'."
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
            *cookie_args,
            "--newline",
            "-f",
            format_selector,
            "--merge-output-format",
            extension,
            "-o",
            target_path,
            yt_url,
        ]

        log(f"Running yt-dlp with format '{format_selector}'.")
        _job_status(job_id, "processing", progress=20)

        output_lines: List[str] = []
        progress_pattern = re.compile(r"(\d{1,3}(?:\.\d+)?)%")
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:  # pragma: no cover - command failure
            fail(f"Failed to invoke yt-dlp: {exc}")
            return

        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.rstrip()
            if not line:
                continue
            output_lines.append(line)
            log(line)
            match = progress_pattern.search(line)
            if match:
                try:
                    progress_value = float(match.group(1))
                except (TypeError, ValueError):
                    continue
                _job_status(job_id, "processing", progress=progress_value)

        process.stdout.close()
        return_code = process.wait()

        if return_code != 0:
            summary = output_lines[-1] if output_lines else "Download failed."
            fail(f"Download failed: {summary[:300]}")
            return

        _job_status(job_id, "processing", progress=100)
        log(f"Success! Video downloaded to '{target_path}'.")
        _mark_job_success(job_id)
    except Exception as exc:  # pragma: no cover - unexpected failure
        fail(f"Unexpected error: {exc}")


@app.route("/jobs", methods=["GET"])
def jobs_index():
    return jsonify({"jobs": jobs_repo.list()})


@app.route("/jobs/<job_id>", methods=["GET"])
def job_detail(job_id: str):
    job = jobs_repo.get(job_id, include_logs=True)
    if job is None:
        return jsonify({"error": "Job not found."}), 404
    return jsonify({"job": job})


def _apply_path_overrides(original_path: str, overrides: List[Dict[str, str]]) -> Optional[str]:
    """Return the first override that matches the Radarr path."""

    normalized_original = os.path.normpath(original_path).replace("\\", "/")

    for entry in overrides:
        remote = os.path.normpath(entry["remote"]).replace("\\", "/")
        local = entry["local"]

        if normalized_original == remote:
            candidate = local
        elif normalized_original.startswith(remote + "/"):
            remainder = normalized_original[len(remote) + 1 :]
            candidate = os.path.join(local, remainder)
        else:
            continue

        candidate_path = os.path.normpath(candidate)
        if os.path.isdir(candidate_path):
            return candidate_path

    return None


def resolve_movie_path(original_path: Optional[str], config: Dict) -> Optional[str]:
    """Resolve a movie folder path using configured library paths."""

    if original_path and os.path.isdir(original_path):
        return original_path

    if not original_path:
        return None

    normalized_original = os.path.normpath(str(original_path)).replace("\\", "/")

    for override in config.get("path_overrides", []):
        remote = (override.get("remote") or "").strip()
        local = (override.get("local") or "").strip()
        if not remote or not local:
            continue
        remote_normalized = os.path.normpath(remote).replace("\\", "/")
        if normalized_original == remote_normalized:
            remainder = ""
        elif normalized_original.startswith(remote_normalized + "/"):
            remainder = normalized_original[len(remote_normalized) + 1 :]
        else:
            continue
        candidate = os.path.normpath(os.path.join(local, remainder)) if remainder else local
        if os.path.isdir(candidate):
            return candidate

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

    overrides_text = "\n".join(
        f"{item['remote']} => {item['local']}" for item in config.get("path_overrides", [])
    )

    if request.method == "POST":
        radarr_url = (request.form.get("radarr_url") or "").strip().rstrip("/")
        api_key = (request.form.get("radarr_api_key") or "").strip()
        raw_paths = request.form.get("file_paths") or ""
        file_paths = normalize_paths(raw_paths)
        raw_overrides = request.form.get("path_overrides") or ""
        overrides_text = raw_overrides
        override_entries, override_errors = parse_path_overrides(raw_overrides)
        overrides = normalize_path_overrides(override_entries)
        errors.extend(override_errors)

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
                "path_overrides": overrides,
            }
        )

        if not errors:
            save_config(config)
            return redirect(url_for("index"))

    return render_template(
        "setup.html",
        config=config,
        errors=errors,
        configured=is_configured(config),
        overrides_text=overrides_text,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
