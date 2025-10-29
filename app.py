import glob
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import uuid
from typing import Dict, Iterable, List, Optional, Tuple

from glob import glob as glob_paths

import requests
from flask import Flask, jsonify, redirect, render_template, request, url_for

from jobs import JobRepository

app = Flask(__name__)

CONFIG_BASE = os.environ.get("YT2RADARR_CONFIG_DIR", os.path.dirname(__file__))
CONFIG_PATH = os.path.join(CONFIG_BASE, "config.json")
JOBS_PATH = os.path.join(CONFIG_BASE, "jobs.json")
DEFAULT_COOKIE_FILENAME = "cookies.txt"

# Prefer higher bitrate HLS/H.264 streams before falling back to DASH/AV1.
# YouTube often serves low bitrate AV1 streams as "best", so bias toward
# muxed or H.264/AAC combinations at the highest available resolution and only
# allow other codecs when no higher quality HLS/H.264 options are available.
YTDLP_FORMAT_SELECTOR = (
    "bestvideo[height>=2160][vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
    "bestvideo[height>=1440][vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
    "bestvideo[height>=1080][vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
    "bestvideo[height>=720][vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
    "bestvideo[height>=2160]+bestaudio/"
    "bestvideo[height>=1440]+bestaudio/"
    "bestvideo[height>=1080]+bestaudio/"
    "bestvideo[height>=720]+bestaudio/"
    "95/"
    "best"
)


def _format_filesize(value: Optional[float]) -> str:
    """Return a human-readable string for a byte size."""

    if value is None:
        return "unknown"
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if size <= 0:
        return "unknown"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    return f"{size:.1f} {units[index]}"

def _default_config() -> Dict:
    return {
        "radarr_url": (os.environ.get("RADARR_URL") or "").rstrip("/"),
        "radarr_api_key": os.environ.get("RADARR_API_KEY") or "",
        "file_paths": [],
        "path_overrides": [],
        "debug_mode": bool(os.environ.get("YT2RADARR_DEBUG", "").strip()),
        "cookie_file": "",
    }


_config_cache: Optional[Dict] = None

_movies_cache: Optional[List[Dict]] = None

jobs_repo = JobRepository(JOBS_PATH, max_items=50)


def append_job_log(job_id: str, message: str) -> None:
    jobs_repo.append_logs(job_id, [message])


def replace_job_log(job_id: str, message: str) -> None:
    jobs_repo.replace_last_log(job_id, message)


def _mark_job_failure(job_id: str, message: str) -> None:
    jobs_repo.mark_failure(job_id, message)


def _mark_job_success(job_id: str) -> None:
    jobs_repo.mark_success(job_id)


def _job_status(job_id: str, status: str, progress: Optional[float] = None) -> None:
    jobs_repo.status(job_id, status, progress=progress)


_NOISY_WARNING_SNIPPETS = (
    "[youtube]",
    "sabr streaming",
    "web client https formats have been skipped",
    "web_safari client https formats have been skipped",
    "tv client https formats have been skipped",
)

_ESSENTIAL_PHRASES = (
    "success! video saved",
    "renaming downloaded file",
    "treating video as main video file",
    "storing video in subfolder",
    "created movie folder",
    "fetching radarr details",
    "resolved youtube format",
)


def _filter_logs_for_display(logs: Iterable[str], debug_mode: bool) -> List[str]:
    filtered: List[str] = []
    for raw in logs or []:
        text = str(raw)
        trimmed = text.strip()
        if not trimmed:
            continue
        if debug_mode:
            filtered.append(trimmed)
            continue

        lowered = trimmed.lower()
        if lowered.startswith("debug:"):
            continue

        if lowered.startswith("warning:") and any(
            snippet in lowered for snippet in _NOISY_WARNING_SNIPPETS
        ):
            continue

        if lowered.startswith(("error:", "warning:", "[download]", "[ffmpeg]", "[merger]")):
            filtered.append(trimmed)
            continue

        if any(phrase in lowered for phrase in _ESSENTIAL_PHRASES):
            filtered.append(trimmed)

    return filtered if filtered else []


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

    config["debug_mode"] = bool(config.get("debug_mode"))

    cookie_file = str(config.get("cookie_file") or "").strip()
    if not cookie_file:
        default_candidate = os.path.join(CONFIG_BASE, DEFAULT_COOKIE_FILENAME)
        if os.path.exists(default_candidate):
            cookie_file = DEFAULT_COOKIE_FILENAME
    config["cookie_file"] = cookie_file

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


def _cookie_absolute_path(cookie_file: str) -> str:
    if not cookie_file:
        return ""
    expanded = os.path.expanduser(cookie_file)
    if os.path.isabs(expanded):
        return os.path.abspath(expanded)
    return os.path.abspath(os.path.join(CONFIG_BASE, expanded))


def _secure_cookie_file(path: str) -> None:
    if not path:
        return
    try:
        if os.name == "nt":
            os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
        else:
            os.chmod(path, 0o600)
    except OSError:
        pass


def get_cookie_path(config: Optional[Dict] = None) -> str:
    env_path = os.environ.get("YT_COOKIE_FILE")
    if env_path:
        absolute = _cookie_absolute_path(env_path)
        if os.path.exists(absolute):
            _secure_cookie_file(absolute)
            return absolute
    cfg = config or load_config()
    cookie_file = str(cfg.get("cookie_file") or "").strip()
    absolute = _cookie_absolute_path(cookie_file)
    if absolute and os.path.exists(absolute):
        _secure_cookie_file(absolute)
        return absolute
    return ""


def save_cookie_text(raw_text: str) -> str:
    os.makedirs(CONFIG_BASE or ".", exist_ok=True)
    cookie_file = DEFAULT_COOKIE_FILENAME
    target_path = _cookie_absolute_path(cookie_file)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    mode = 0o600 if os.name != "nt" else 0o666
    with os.fdopen(os.open(target_path, flags, mode), "w", encoding="utf-8") as handle:
        handle.write(raw_text.strip() + "\n")
    _secure_cookie_file(target_path)
    return cookie_file


def delete_cookie_file(cookie_file: str) -> None:
    absolute = _cookie_absolute_path(cookie_file)
    if not absolute:
        return
    try:
        if os.path.exists(absolute):
            os.remove(absolute)
    except OSError:
        pass


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


def build_movie_stem(movie: Dict) -> str:
    """Return the canonical movie stem ``Title (Year) {tmdb-ID}``."""

    title = str(movie.get("title") or "Movie").strip()
    year = str(movie.get("year") or "").strip()
    tmdb_id = str(movie.get("tmdbId") or "").strip()

    parts = [title]
    if year:
        parts.append(f"({year})")
    if tmdb_id:
        parts.append(f"{{tmdb-{tmdb_id}}}")

    stem = " ".join(parts)
    cleaned = sanitize_filename(stem)
    return cleaned or "Movie"


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
    metadata = []
    if extra:
        metadata.append("Stored as extra content")
    return {"label": label or "Radarr Download", "subtitle": subtitle, "metadata": metadata}


@app.route("/", methods=["GET"])
def index():
    movies = get_all_movies()
    config = load_config()
    return render_template(
        "index.html",
        movies=movies,
        configured=is_configured(config),
        debug_mode=config.get("debug_mode", False),
    )
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

    if errors:
        return jsonify({"logs": logs}), 400

    payload = {
        "yturl": yt_url,
        "movieId": movie_id,
        "movieName": (data.get("movieName") or "").strip(),
        "title": (data.get("title") or "").strip(),
        "year": (data.get("year") or "").strip(),
        "tmdb": (data.get("tmdb") or "").strip(),
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

    display_job = dict(job_record)
    display_job["logs"] = _filter_logs_for_display(
        display_job.get("logs", []), config.get("debug_mode", False)
    )

    return jsonify({"job": display_job, "debug_mode": config.get("debug_mode", False)}), 202


def process_download_job(job_id: str, payload: Dict) -> None:
    def log(message: str) -> None:
        append_job_log(job_id, message)

    def warn(message: str) -> None:
        append_job_log(job_id, f"WARNING: {message}")

    def fail(message: str) -> None:
        append_job_log(job_id, f"ERROR: {message}")
        _mark_job_failure(job_id, message)

    def debug(message: str) -> None:
        append_job_log(job_id, f"DEBUG: {message}")

    try:
        _job_status(job_id, "processing", progress=1)
        config = load_config()
        if not is_configured(config):
            fail("Application has not been configured yet.")
            return

        debug_enabled = bool(config.get("debug_mode"))
        compact_progress_logs = not debug_enabled
        cookie_path = get_cookie_path(config)

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
        resolved_path, created_folder = resolve_movie_path(
            movie_path, config, create_if_missing=True
        )
        if resolved_path is None:
            fail(f"Movie folder not found on disk: {movie_path}")
            return

        movie_path = resolved_path
        if created_folder:
            log(f"Created movie folder at '{movie_path}'.")
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
            log("Treating video as main video file.")

        movie_stem = build_movie_stem(movie)
        log(f"Resolved Radarr movie stem to '{movie_stem}'.")

        canonical_stem = movie_stem
        extra_label = ""
        if extra:
            extra_label = sanitize_filename(extra_name) or EXTRA_TYPE_LABELS.get(
                extra_type, extra_type.capitalize()
            )
            if extra_label:
                canonical_stem = f"{movie_stem} {extra_label}"
                log(f"Using extra label '{extra_label}'.")

        descriptive = extra_name
        if descriptive:
            log(f"Using custom descriptive name '{descriptive}'.")
        else:
            try:
                log("Querying yt-dlp for video title.")
                yt_cmd = [
                    "yt-dlp",
                    "--get-title",
                ]
                if cookie_path:
                    yt_cmd += ["--cookies", cookie_path]
                yt_cmd.append(yt_url)
                proc = subprocess.run(
                    yt_cmd,
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

        descriptive = sanitize_filename(descriptive) or "Video"

        if extra:
            extra_suffix = sanitize_filename(extra_name) or extra_type
            if extra_suffix:
                filename_base = f"{descriptive}-{extra_suffix}"
            else:
                filename_base = descriptive
        else:
            filename_base = descriptive

        filename_base = filename_base or "Video"
        pattern = os.path.join(target_dir, f"{filename_base}.*")
        if any(os.path.exists(path) for path in glob_paths(pattern)):
            log(f"File stem '{filename_base}' already exists. Searching for a free filename.")
            index = 1
            while True:
                candidate_base = f"{filename_base} ({index})"
                candidate_pattern = os.path.join(target_dir, f"{candidate_base}.*")
                if not any(os.path.exists(path) for path in glob_paths(candidate_pattern)):
                    filename_base = candidate_base
                    log(f"Selected new filename stem '{filename_base}'.")
                    break
                index += 1

        template_base = filename_base.replace("%", "%%")
        target_template = os.path.join(target_dir, f"{template_base}.%(ext)s")
        expected_pattern = os.path.join(target_dir, f"{filename_base}.*")

        if shutil.which("ffmpeg") is None:
            warn(
                "ffmpeg executable not found; yt-dlp may fall back to a lower quality progressive stream."
            )

        progress_pattern = re.compile(r"(\d{1,3}(?:\.\d+)?)%")
        format_selector = YTDLP_FORMAT_SELECTOR

        info_command = ["yt-dlp"]
        if cookie_path:
            info_command += ["--cookies", cookie_path]
        info_command += [
            "-f",
            format_selector,
            "--skip-download",
            "--print-json",
            yt_url,
        ]

        resolved_format: Dict[str, str] = {}
        try:
            info_result = subprocess.run(
                info_command,
                capture_output=True,
                text=True,
                check=True,
            )
        except Exception as exc:  # pragma: no cover - command failure
            warn(f"Failed to query format details via yt-dlp: {exc}")
        else:
            info_payload: Optional[Dict] = None
            for raw_line in info_result.stdout.splitlines():
                stripped = raw_line.strip()
                if not stripped:
                    continue
                try:
                    info_payload = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                else:
                    break

            if info_payload:
                requested_formats = info_payload.get("requested_formats") or []
                if requested_formats:
                    video_format = next(
                        (entry for entry in requested_formats if entry.get("vcodec") not in (None, "none")),
                        None,
                    )
                    audio_format = next(
                        (entry for entry in requested_formats if entry.get("acodec") not in (None, "none")),
                        None,
                    )
                    format_ids = [
                        entry.get("format_id")
                        for entry in requested_formats
                        if entry.get("format_id")
                    ]
                    width_value = None
                    height_value = None
                    if video_format:
                        width_value = video_format.get("width") or info_payload.get("width")
                        height_value = video_format.get("height") or info_payload.get("height")
                    else:
                        width_value = info_payload.get("width")
                        height_value = info_payload.get("height")
                    vcodec_value = (video_format or {}).get("vcodec") or info_payload.get("vcodec")
                    acodec_value = (audio_format or {}).get("acodec") or info_payload.get("acodec")
                    total_size: Optional[float] = None
                    size_components: List[float] = []
                    for entry in requested_formats:
                        for key in ("filesize", "filesize_approx"):
                            candidate = entry.get(key)
                            if isinstance(candidate, (int, float)) and candidate > 0:
                                size_components.append(float(candidate))
                                break
                    if size_components:
                        total_size = sum(size_components)
                    resolution = "unknown"
                    if width_value and height_value:
                        resolution = f"{int(width_value)}x{int(height_value)}"
                    format_id_value = "+".join(format_ids) if format_ids else "unknown"
                    resolved_format = {
                        "format_id": format_id_value,
                        "resolution": resolution,
                        "video_codec": vcodec_value or "unknown",
                        "audio_codec": acodec_value or "unknown",
                        "filesize": _format_filesize(total_size),
                    }
                else:
                    format_id_value = info_payload.get("format_id") or "unknown"
                    width_value = info_payload.get("width")
                    height_value = info_payload.get("height")
                    resolution = "unknown"
                    if width_value and height_value:
                        resolution = f"{int(width_value)}x{int(height_value)}"
                    resolved_format = {
                        "format_id": str(format_id_value),
                        "resolution": resolution,
                        "video_codec": info_payload.get("vcodec") or "unknown",
                        "audio_codec": info_payload.get("acodec") or "unknown",
                        "filesize": _format_filesize(
                            info_payload.get("filesize") or info_payload.get("filesize_approx")
                        ),
                    }

            if resolved_format:
                log(
                    "Resolved YouTube format: "
                    f"id={resolved_format['format_id']}, "
                    f"resolution={resolved_format['resolution']}, "
                    f"video_codec={resolved_format['video_codec']}, "
                    f"audio_codec={resolved_format['audio_codec']}, "
                    f"filesize={resolved_format['filesize']}"
                )
            else:
                log("yt-dlp did not report a resolved format; proceeding with download.")

        command = ["yt-dlp"]
        if cookie_path:
            command += ["--cookies", cookie_path]
        command += ["--newline"]
        command += ["-f", format_selector, "-o", target_template, yt_url]

        log("Running yt-dlp with explicit output template.")

        _job_status(job_id, "processing", progress=20)

        output_lines: List[str] = []
        progress_log_active = False
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=target_dir,
            )
        except Exception as exc:  # pragma: no cover - command failure
            fail(f"Failed to invoke yt-dlp: {exc}")
            return

        assert process.stdout is not None

        debug_prefixes = (
            "[debug]",
            "[info]",
            "[extractor]",
            "[metadata]",
            "[youtube]",
        )

        def handle_output_line(text: str) -> None:
            nonlocal progress_log_active

            line = text.strip()
            if not line:
                return
            output_lines.append(line)
            match = progress_pattern.search(line)
            if match:
                try:
                    progress_value = float(match.group(1))
                except (TypeError, ValueError):
                    progress_value = None
                if progress_value is not None:
                    _job_status(job_id, "processing", progress=progress_value)
                if line.startswith("[download]"):
                    if compact_progress_logs:
                        if not progress_log_active:
                            append_job_log(job_id, line)
                            progress_log_active = True
                        else:
                            replace_job_log(job_id, line)
                    else:
                        append_job_log(job_id, line)
                    return
            lowered = line.lower()
            if "error" in lowered:
                append_job_log(job_id, f"ERROR: {line}")
                return
            if "warning" in lowered:
                warn(line)
                return
            if line.startswith("[download]") or line.startswith("[ffmpeg]"):
                log(line)
                return
            if line.lower().startswith(debug_prefixes):
                debug(line)
                return
            log(line)

        for raw_line in process.stdout:
            line = raw_line.rstrip()
            if not line:
                continue
            handle_output_line(line)

        process.stdout.close()
        return_code = process.wait()

        if return_code != 0:
            failure_summary = output_lines[-1] if output_lines else "Download failed."
            log(f"yt-dlp exited with code {return_code}.")

            for leftover in glob_paths(expected_pattern):
                if leftover.endswith(".part") or leftover.endswith(".ytdl"):
                    try:
                        os.remove(leftover)
                    except OSError:
                        continue

            fail(f"Download failed: {failure_summary[:300]}")
            return

        downloaded_candidates = [
            path
            for path in glob_paths(expected_pattern)
            if os.path.isfile(path) and not path.endswith((".part", ".ytdl"))
        ]

        def _is_intermediate_file(name: str) -> bool:
            base = os.path.basename(name)
            if base.endswith(".temp") or ".temp." in base:
                return True
            return bool(re.search(r"\.f\d+\.\w+$", base))

        if not downloaded_candidates:
            fail("Download completed but the output file could not be located.")
            return

        final_candidates = [
            path for path in downloaded_candidates if not _is_intermediate_file(path)
        ]

        if final_candidates:
            target_path = max(final_candidates, key=os.path.getmtime)
        else:
            downloaded_candidates.sort(key=os.path.getmtime, reverse=True)
            target_path = downloaded_candidates[0]
        actual_extension = os.path.splitext(target_path)[1].lstrip(".").lower()

        job_snapshot = jobs_repo.get(job_id)
        if job_snapshot:
            metadata = list(job_snapshot.get("metadata") or [])
            updated_metadata: List[str] = []
            def _should_keep(entry: object) -> bool:
                if not isinstance(entry, str):
                    return True
                lowered = entry.lower()
                prefixes = [
                    "format:",
                    "format id:",
                    "resolution:",
                    "video codec:",
                    "audio codec:",
                    "filesize:",
                ]
                return not any(lowered.startswith(prefix) for prefix in prefixes)

            for item in metadata:
                if _should_keep(item):
                    updated_metadata.append(item)

            if actual_extension:
                updated_metadata.append(f"Format: {actual_extension.upper()}")
            if resolved_format.get("format_id"):
                updated_metadata.append(f"Format ID: {resolved_format['format_id']}")
            if resolved_format.get("resolution") and resolved_format["resolution"] != "unknown":
                updated_metadata.append(f"Resolution: {resolved_format['resolution']}")
            if resolved_format.get("video_codec") and resolved_format["video_codec"] != "unknown":
                updated_metadata.append(f"Video Codec: {resolved_format['video_codec']}")
            if resolved_format.get("audio_codec") and resolved_format["audio_codec"] != "unknown":
                updated_metadata.append(f"Audio Codec: {resolved_format['audio_codec']}")
            if resolved_format.get("filesize") and resolved_format["filesize"] != "unknown":
                updated_metadata.append(f"Filesize: {resolved_format['filesize']}")

            jobs_repo.update(job_id, {"metadata": updated_metadata})

        if actual_extension:
            canonical_filename = f"{canonical_stem}.{actual_extension}"
        else:
            canonical_filename = canonical_stem
        canonical_path = os.path.join(target_dir, canonical_filename)
        if os.path.exists(canonical_path):
            base_name, ext_part = os.path.splitext(canonical_filename)
            log(
                f"Canonical filename '{canonical_filename}' already exists. Searching for a free name."
            )
            index = 1
            while True:
                new_filename = f"{base_name} ({index}){ext_part}"
                candidate = os.path.join(target_dir, new_filename)
                if not os.path.exists(candidate):
                    canonical_filename = new_filename
                    canonical_path = candidate
                    log(f"Selected canonical filename '{new_filename}'.")
                    break
                index += 1

        try:
            if os.path.abspath(target_path) != os.path.abspath(canonical_path):
                log(
                    f"Renaming downloaded file to canonical name '{canonical_filename}'."
                )
                os.replace(target_path, canonical_path)
                target_path = canonical_path
            else:
                log("Download already matches canonical filename.")
        except Exception as exc:
            fail(
                f"Failed to rename downloaded file to '{canonical_filename}': {exc}"
            )
            return

        for leftover in downloaded_candidates:
            if os.path.abspath(leftover) == os.path.abspath(target_path):
                continue
            if not _is_intermediate_file(leftover):
                continue
            try:
                os.remove(leftover)
            except OSError:
                continue

        _job_status(job_id, "processing", progress=100)
        log(f"Success! Video saved as '{target_path}'.")
        _mark_job_success(job_id)
    except Exception as exc:  # pragma: no cover - unexpected failure
        fail(f"Unexpected error: {exc}")


@app.route("/jobs", methods=["GET"])
def jobs_index():
    config = load_config()
    return jsonify(
        {
            "jobs": jobs_repo.list(),
            "debug_mode": config.get("debug_mode", False),
        }
    )


@app.route("/jobs/<job_id>", methods=["GET"])
def job_detail(job_id: str):
    config = load_config()
    job = jobs_repo.get(job_id, include_logs=True)
    if job is None:
        return jsonify({"error": "Job not found."}), 404
    job["logs"] = _filter_logs_for_display(job.get("logs", []), config.get("debug_mode", False))
    return jsonify({"job": job, "debug_mode": config.get("debug_mode", False)})


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


def resolve_movie_path(
    original_path: Optional[str],
    config: Dict,
    *,
    create_if_missing: bool = False,
) -> Tuple[Optional[str], bool]:
    """Resolve a movie folder path using configured library paths.

    Returns a tuple of ``(path, created)`` where ``created`` indicates whether
    the directory was created during resolution.
    """

    created = False

    def ensure_candidate(candidate: str, base_dir: Optional[str]) -> Optional[str]:
        nonlocal created
        if os.path.isdir(candidate):
            return candidate
        if not create_if_missing:
            return None
        candidate_base = base_dir or os.path.dirname(candidate)
        if not candidate_base or not os.path.isdir(candidate_base):
            return None
        try:
            os.makedirs(candidate, exist_ok=True)
        except OSError:
            return None
        created = True
        return candidate

    if original_path:
        normalized_path = os.path.normpath(str(original_path))
        if os.path.isdir(normalized_path):
            return normalized_path, created
        direct_candidate = ensure_candidate(
            normalized_path, os.path.dirname(normalized_path)
        )
        if direct_candidate:
            return direct_candidate, created
    else:
        return None, created

    normalized_original = normalized_path.replace("\\", "/")

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
        candidate = (
            os.path.normpath(os.path.join(local, remainder)) if remainder else local
        )
        base_dir = os.path.dirname(candidate) if remainder else None
        resolved = ensure_candidate(candidate, base_dir or local)
        if resolved:
            return resolved, created

    folder_name = os.path.basename(normalized_path.rstrip(os.sep))
    if not folder_name:
        return None, created

    for base_path in config.get("file_paths", []):
        candidate = os.path.join(base_path, folder_name)
        resolved = ensure_candidate(candidate, base_path)
        if resolved:
            return resolved, created

    return None, created


@app.route("/setup", methods=["GET", "POST"])
def setup():
    config = load_config().copy()
    errors: List[str] = []

    overrides_text = "\n".join(
        f"{item['remote']} => {item['local']}" for item in config.get("path_overrides", [])
    )

    cookie_preview = ""

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
        debug_mode = bool(request.form.get("debug_mode"))

        cookie_text = request.form.get("cookie_text") or ""
        cookie_preview = cookie_text
        clear_cookies = bool(request.form.get("clear_cookies"))

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
                "debug_mode": debug_mode,
            }
        )

        if not errors:
            if cookie_text.strip():
                config["cookie_file"] = save_cookie_text(cookie_text)
            elif clear_cookies:
                delete_cookie_file(config.get("cookie_file", ""))
                config["cookie_file"] = ""
            save_config(config)
            return redirect(url_for("index"))

    return render_template(
        "setup.html",
        config=config,
        errors=errors,
        configured=is_configured(config),
        overrides_text=overrides_text,
        cookie_preview=cookie_preview,
        cookie_env_path=os.environ.get("YT_COOKIE_FILE", ""),
        resolved_cookie_path=get_cookie_path(config),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
