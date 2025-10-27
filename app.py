import json
import os
import re
import subprocess
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import requests
from flask import Flask, jsonify, redirect, render_template, request, url_for

app = Flask(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
JOBS_PATH = os.path.join(os.path.dirname(__file__), "jobs.json")

MAX_STORED_JOBS = 50


def _default_config() -> Dict:
    return {
        "radarr_url": (os.environ.get("RADARR_URL") or "").rstrip("/"),
        "radarr_api_key": os.environ.get("RADARR_API_KEY") or "",
        "file_paths": [],
    }


_config_cache: Optional[Dict] = None

_movies_cache: Optional[List[Dict]] = None

_jobs_cache: Optional[List[Dict]] = None

_jobs_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _ensure_jobs_loaded() -> None:
    global _jobs_cache
    if _jobs_cache is not None:
        return
    try:
        with open(JOBS_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
            if isinstance(data, list):
                _jobs_cache = data
            else:
                _jobs_cache = []
    except FileNotFoundError:
        _jobs_cache = []
    except Exception as exc:  # pragma: no cover - history file errors
        print(f"Failed to load jobs history: {exc}")
        _jobs_cache = []


def _persist_jobs_locked() -> None:
    os.makedirs(os.path.dirname(JOBS_PATH) or ".", exist_ok=True)
    with open(JOBS_PATH, "w", encoding="utf-8") as handle:
        json.dump(_jobs_cache or [], handle, indent=2)


def _serialize_job(job: Dict, include_logs: bool = False) -> Dict:
    payload = {
        "id": job.get("id"),
        "status": job.get("status"),
        "progress": job.get("progress", 0),
        "label": job.get("label"),
        "subtitle": job.get("subtitle"),
        "metadata": job.get("metadata", []),
        "message": job.get("message"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "updated_at": job.get("updated_at"),
        "completed_at": job.get("completed_at"),
    }
    if include_logs:
        payload["logs"] = job.get("logs", [])
    return payload


def list_jobs(include_logs: bool = False) -> List[Dict]:
    with _jobs_lock:
        _ensure_jobs_loaded()
        jobs = list(_jobs_cache or [])
    jobs.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return [_serialize_job(job, include_logs=include_logs) for job in jobs]


def get_job(job_id: str) -> Optional[Dict]:
    with _jobs_lock:
        _ensure_jobs_loaded()
        for job in _jobs_cache or []:
            if job.get("id") == job_id:
                return job
    return None


def _update_job(job_id: str, updates: Dict) -> Optional[Dict]:
    with _jobs_lock:
        _ensure_jobs_loaded()
        if not _jobs_cache:
            return None
        for index, job in enumerate(_jobs_cache):
            if job.get("id") != job_id:
                continue
            current_progress = float(job.get("progress") or 0.0)
            if "progress" in updates:
                try:
                    new_progress = float(updates["progress"])
                except (TypeError, ValueError):
                    new_progress = current_progress
                updates["progress"] = max(current_progress, min(100.0, max(0.0, new_progress)))
            job.update(updates)
            job["updated_at"] = _now_iso()
            _jobs_cache[index] = job
            _jobs_cache[:] = _jobs_cache[:MAX_STORED_JOBS]
            _persist_jobs_locked()
            return job
    return None


def append_job_log(job_id: str, message: str) -> None:
    with _jobs_lock:
        _ensure_jobs_loaded()
        if not _jobs_cache:
            return
        for index, job in enumerate(_jobs_cache):
            if job.get("id") != job_id:
                continue
            logs = job.setdefault("logs", [])
            logs.append(str(message))
            if len(logs) > 200:
                job["logs"] = logs[-200:]
            job["updated_at"] = _now_iso()
            _jobs_cache[index] = job
            _persist_jobs_locked()
            return


def record_job(job: Dict) -> Dict:
    global _jobs_cache
    with _jobs_lock:
        _ensure_jobs_loaded()
        cache = _jobs_cache or []
        cache.insert(0, job)
        _jobs_cache = cache[:MAX_STORED_JOBS]
        _persist_jobs_locked()
    return job


def _mark_job_failure(job_id: str, message: str) -> None:
    _update_job(
        job_id,
        {
            "status": "failed",
            "message": message,
            "progress": 100,
            "completed_at": _now_iso(),
        },
    )


def _mark_job_success(job_id: str) -> None:
    _update_job(
        job_id,
        {
            "status": "complete",
            "message": "",
            "progress": 100,
            "completed_at": _now_iso(),
        },
    )


def _job_status(job_id: str, status: str, progress: Optional[float] = None) -> None:
    updates: Dict = {"status": status}
    if progress is not None:
        updates["progress"] = progress
    if status == "processing":
        job = get_job(job_id)
        if job and not job.get("started_at"):
            updates["started_at"] = _now_iso()
    _update_job(job_id, updates)


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
    elif not re.search(r"(youtube\\.com|youtu\\.be)/", yt_url):
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
    timestamp = _now_iso()
    job_record = {
        "id": job_id,
        "status": "queued",
        "progress": 0,
        "label": descriptors["label"],
        "subtitle": descriptors["subtitle"],
        "metadata": descriptors["metadata"],
        "message": "",
        "logs": ["Job queued."],
        "created_at": timestamp,
        "started_at": None,
        "updated_at": timestamp,
        "completed_at": None,
        "request": payload,
    }

    record_job(job_record)

    worker = threading.Thread(target=process_download_job, args=(job_id, payload), daemon=True)
    worker.start()

    return jsonify({"job": _serialize_job(job_record, include_logs=True)}), 202


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
        _update_job(
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
                    ["yt-dlp", "--get-title", yt_url],
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
    return jsonify({"jobs": list_jobs()})


@app.route("/jobs/<job_id>", methods=["GET"])
def job_detail(job_id: str):
    job = get_job(job_id)
    if job is None:
        return jsonify({"error": "Job not found."}), 404
    return jsonify({"job": _serialize_job(job, include_logs=True)})


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
