"""Flask application that bridges YouTube downloads into Radarr."""

# pylint: disable=too-many-lines

import itertools
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import uuid
import selectors
from dataclasses import dataclass
from collections.abc import Iterable
from typing import Any, Callable, Dict, List, Optional, Tuple

from glob import glob as glob_paths

import requests  # pylint: disable=import-error
from flask import (  # pylint: disable=import-error
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from yt_dlp import YoutubeDL  # pylint: disable=import-error
from yt_dlp.extractor.youtube import YoutubeSearchIE  # pylint: disable=import-error
from yt_dlp.utils import YoutubeDLError  # pylint: disable=import-error

from jobs import JobRepository


@dataclass
class JobControl:
    """Track runtime details for an active download worker."""

    thread: threading.Thread
    cancel_event: threading.Event
    process: Optional[subprocess.Popen] = None


class JobCancelled(Exception):
    """Raised when a download job has been cancelled by the user."""


_JOB_CONTROLS: Dict[str, JobControl] = {}
_JOB_CONTROLS_LOCK = threading.Lock()

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
METADATA_FETCH_TIMEOUT_SECONDS = 120



YOUTUBE_SEARCH_MAX_RESULTS = 20
YOUTUBE_SEARCH_CACHE_TTL = 90.0

_YOUTUBE_SEARCH_CACHE: Dict[Tuple[str, int], Tuple[float, List[Dict[str, Any]]]] = {}
_YOUTUBE_SEARCH_LOCK = threading.Lock()

YOUTUBE_SEARCH_DL_OPTIONS = {
    "quiet": True,
    "skip_download": True,
    "extract_flat": True,
    "noplaylist": True,
    "cachedir": False,
    "socket_timeout": 10,
    "retries": 1,
    "extractor_retries": 0,
    "nocheckcertificate": True,
}
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
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    return f"{size:.1f} {units[unit_index]}"
def _default_config() -> Dict:
    return {
        "radarr_url": (os.environ.get("RADARR_URL") or "").rstrip("/"),
        "radarr_api_key": os.environ.get("RADARR_API_KEY") or "",
        "file_paths": [],
        "path_overrides": [],
        "debug_mode": bool(os.environ.get("YT2RADARR_DEBUG", "").strip()),
        "cookie_file": "",
    }


_CACHE: Dict[str, Optional[Any]] = {"config": None, "movies": None}

jobs_repo = JobRepository(JOBS_PATH, max_items=50)


def append_job_log(job_id: str, message: str) -> None:
    """Append a single log message to the given job."""
    jobs_repo.append_logs(job_id, [message])


def replace_job_log(job_id: str, message: str) -> None:
    """Replace the most recent log entry for a job."""
    jobs_repo.replace_last_log(job_id, message)


def _mark_job_failure(job_id: str, message: str) -> None:
    """Mark the specified job as failed."""
    jobs_repo.mark_failure(job_id, message)


def _mark_job_success(job_id: str) -> None:
    """Mark the specified job as successful."""
    jobs_repo.mark_success(job_id)


def _mark_job_cancelled(job_id: str, message: str = "Job cancelled by user.") -> None:
    """Mark the specified job as cancelled."""

    jobs_repo.mark_cancelled(job_id, message)


def _job_status(job_id: str, status: str, progress: Optional[float] = None) -> None:
    """Persist a status update for a job."""
    jobs_repo.status(job_id, status, progress=progress)


def _register_job_control(
    job_id: str, worker: threading.Thread, cancel_event: threading.Event
) -> None:
    """Store the worker and cancellation event for an active job."""

    control = JobControl(thread=worker, cancel_event=cancel_event)
    with _JOB_CONTROLS_LOCK:
        _JOB_CONTROLS[job_id] = control


def _set_job_process(job_id: str, process: Optional[subprocess.Popen]) -> None:
    """Record the active subprocess for a running job."""

    with _JOB_CONTROLS_LOCK:
        control = _JOB_CONTROLS.get(job_id)
        if control is not None:
            control.process = process


def _clear_job_process(job_id: str) -> None:
    """Clear any tracked subprocess for the specified job."""

    with _JOB_CONTROLS_LOCK:
        control = _JOB_CONTROLS.get(job_id)
        if control is not None:
            control.process = None


def _unregister_job_control(job_id: str) -> None:
    """Remove tracking information for a completed job."""

    with _JOB_CONTROLS_LOCK:
        _JOB_CONTROLS.pop(job_id, None)


def _terminate_process(process: Optional[subprocess.Popen]) -> None:
    """Attempt to gracefully stop a running subprocess."""

    if process is None:
        return
    try:
        process.terminate()
    except OSError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass


def _cleanup_temp_files(pattern: Optional[str]) -> None:
    """Remove temporary download fragments produced by yt-dlp."""

    if not pattern:
        return
    for leftover in glob_paths(pattern):
        if leftover.endswith((".part", ".ytdl")):
            try:
                os.remove(leftover)
            except OSError:
                continue


def _normalise_youtube_result(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convert a YouTube search entry into the structure expected by the UI."""

    url = entry.get("url")
    if not url:
        video_id = entry.get("id")
        if isinstance(video_id, str):
            url = f"https://www.youtube.com/watch?v={video_id}"
    if not url:
        return None

    view_count = entry.get("view_count")
    if view_count is None:
        view_count = entry.get("concurrent_view_count")

    return {
        "id": entry.get("id"),
        "title": entry.get("title"),
        "url": url,
        "uploader": entry.get("uploader") or entry.get("channel"),
        "viewCount": view_count,
        "duration": entry.get("duration"),
    }


def _iter_youtube_entries(playlist: Any) -> Iterable[Dict[str, Any]]:
    """Yield raw entries from a YouTube search playlist result."""

    if not isinstance(playlist, dict):
        return ()
    entries = playlist.get("entries")
    if not isinstance(entries, Iterable) or isinstance(entries, (str, bytes)):
        return ()
    return entries


def _get_cached_youtube_results(
    cache_key: Tuple[str, int], now: float
) -> Optional[List[Dict[str, Any]]]:
    """Return cached YouTube search results if they are still fresh."""

    with _YOUTUBE_SEARCH_LOCK:
        cached = _YOUTUBE_SEARCH_CACHE.get(cache_key)
        if cached and now - cached[0] < YOUTUBE_SEARCH_CACHE_TTL:
            return [dict(item) for item in cached[1]]
    return None


def _store_youtube_results(
    cache_key: Tuple[str, int], now: float, results: List[Dict[str, Any]]
) -> None:
    """Persist a YouTube search result set and purge stale cache entries."""

    snapshot = [dict(item) for item in results]
    with _YOUTUBE_SEARCH_LOCK:
        _YOUTUBE_SEARCH_CACHE[cache_key] = (now, snapshot)
        stale_keys = [
            key
            for key, (timestamp, _) in list(_YOUTUBE_SEARCH_CACHE.items())
            if now - timestamp >= YOUTUBE_SEARCH_CACHE_TTL
        ]
        for stale_key in stale_keys:
            _YOUTUBE_SEARCH_CACHE.pop(stale_key, None)


def _search_youtube(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Return metadata for the top YouTube matches for the provided query."""

    search_terms = query.strip()
    if not search_terms:
        return []
    try:
        max_results = int(limit or 1)
    except (TypeError, ValueError):
        max_results = 1
    max_results = max(1, min(max_results, YOUTUBE_SEARCH_MAX_RESULTS))
    cache_key = (search_terms.lower(), max_results)
    now = time.monotonic()
    cached = _get_cached_youtube_results(cache_key, now)
    if cached is not None:
        return cached

    try:
        downloader = YoutubeDL(YOUTUBE_SEARCH_DL_OPTIONS.copy())
        searcher = YoutubeSearchIE(downloader)
        playlist = searcher.extract(f"ytsearch{max_results}:{search_terms}")
    except YoutubeDLError as exc:
        raise RuntimeError(f"YouTube search failed: {exc}") from exc
    except Exception as exc:  # pragma: no cover - defensive, constructor shouldn't fail
        raise RuntimeError(f"Failed to initialise YouTube search: {exc}") from exc

    results: List[Dict[str, Any]] = []
    for entry in itertools.islice(_iter_youtube_entries(playlist), max_results):
        if not isinstance(entry, dict):
            continue
        normalised = _normalise_youtube_result(downloader.sanitize_info(entry))
        if normalised is not None:
            results.append(normalised)

    _store_youtube_results(cache_key, now, results)
    return results


def _cleanup_playlist_dir(path: Optional[str]) -> None:
    """Remove the temporary playlist staging directory if it exists."""

    if path and os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)

def _resolve_requested_format(info_payload: Dict[str, Any]) -> Dict[str, str]:
    """Extract a concise summary of the selected YouTube formats."""

    requested_formats = info_payload.get("requested_formats") or []
    if requested_formats:
        video_format = next(
            (
                entry
                for entry in requested_formats
                if entry.get("vcodec") not in (None, "none")
            ),
            None,
        )
        audio_format = next(
            (
                entry
                for entry in requested_formats
                if entry.get("acodec") not in (None, "none")
            ),
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
        return {
            "format_id": format_id_value,
            "resolution": resolution,
            "video_codec": vcodec_value or "unknown",
            "audio_codec": acodec_value or "unknown",
            "filesize": _format_filesize(total_size),
        }

    format_id_value = info_payload.get("format_id") or "unknown"
    width_value = info_payload.get("width")
    height_value = info_payload.get("height")
    resolution = "unknown"
    if width_value and height_value:
        resolution = f"{int(width_value)}x{int(height_value)}"
    return {
        "format_id": str(format_id_value),
        "resolution": resolution,
        "video_codec": info_payload.get("vcodec") or "unknown",
        "audio_codec": info_payload.get("acodec") or "unknown",
        "filesize": _format_filesize(
            info_payload.get("filesize") or info_payload.get("filesize_approx")
        ),
    }


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
    "merging playlist videos",
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

        if lowered.startswith(
            ("error:", "warning:", "[download]", "[ffmpeg]", "[merger]")
        ):
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


def _normalize_loaded_config(raw_config: Optional[Dict]) -> Dict:
    """Merge a raw configuration dictionary with defaults and sanitize values."""

    merged = _default_config()
    if isinstance(raw_config, dict):
        merged.update(raw_config)

    merged["radarr_url"] = (merged.get("radarr_url") or "").strip().rstrip("/")
    merged["radarr_api_key"] = (merged.get("radarr_api_key") or "").strip()

    file_paths = merged.get("file_paths", [])
    if not isinstance(file_paths, list):
        file_paths = [str(file_paths)] if file_paths else []
    merged["file_paths"] = [
        os.path.abspath(os.path.expanduser(str(path))) for path in file_paths
    ]

    overrides_raw = merged.get("path_overrides", [])
    if not isinstance(overrides_raw, list):
        overrides_raw = []
    merged["path_overrides"] = normalize_path_overrides(overrides_raw)

    merged["debug_mode"] = bool(merged.get("debug_mode"))

    cookie_file = str(merged.get("cookie_file") or "").strip()
    if not cookie_file:
        default_candidate = os.path.join(CONFIG_BASE, DEFAULT_COOKIE_FILENAME)
        if os.path.exists(default_candidate):
            cookie_file = DEFAULT_COOKIE_FILENAME
    merged["cookie_file"] = cookie_file

    return merged


def load_config() -> Dict:
    """Load configuration from disk or environment defaults."""

    cached_config = _CACHE.get("config")
    if isinstance(cached_config, dict):
        return cached_config

    config_data: Optional[Dict] = None
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
            if not isinstance(loaded, dict):
                raise ValueError("Invalid configuration format")
            config_data = loaded
    except FileNotFoundError:
        config_data = None
    except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - configuration file errors
        print(f"Failed to load configuration: {exc}")
        config_data = None

    config = _normalize_loaded_config(config_data)
    _CACHE["config"] = config
    return config


def save_config(config: Dict) -> None:
    """Persist configuration to disk and reset caches."""

    os.makedirs(os.path.dirname(CONFIG_PATH) or ".", exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
    _CACHE["config"] = config
    _CACHE["movies"] = None


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


def _split_override_line(cleaned: str) -> Optional[Tuple[str, str]]:
    """Return remote/local components for an override line when possible."""

    for separator in ("=>", "->", ","):
        if separator in cleaned:
            remote_raw, local_raw = cleaned.split(separator, 1)
            return remote_raw.strip(), local_raw.strip()
    return None


def parse_path_overrides(raw_overrides: str) -> Tuple[List[Dict[str, str]], List[str]]:
    """Parse override definitions of the form 'remote => local'."""

    overrides: List[Dict[str, str]] = []
    errors: List[str] = []
    for line_number, line in enumerate(raw_overrides.splitlines(), start=1):
        cleaned = line.strip()
        if not cleaned:
            continue
        split_result = _split_override_line(cleaned)
        if split_result is None:
            errors.append(
                f"Path override line {line_number} must use 'remote => local' format: {cleaned!r}"
            )
            continue
        remote, local = split_result
        if not remote or not local:
            errors.append(
                f"Path override line {line_number} is missing a remote or local path: {cleaned!r}"
            )
            continue
        overrides.append({"remote": remote, "local": local})
    return overrides, errors


def _cookie_absolute_path(cookie_file: str) -> str:
    """Return an absolute cookie file path for a configured value."""
    if not cookie_file:
        return ""
    expanded = os.path.expanduser(cookie_file)
    if os.path.isabs(expanded):
        return os.path.abspath(expanded)
    return os.path.abspath(os.path.join(CONFIG_BASE, expanded))


def _secure_cookie_file(path: str) -> None:
    """Set restrictive permissions on the cookie file when possible."""
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
    """Locate the cookie file, preferring environment overrides."""
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
    """Persist cookie text to disk and return the relative filename."""
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
    """Delete the configured cookie file if it exists."""
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
    cached_movies = _CACHE.get("movies")
    if isinstance(cached_movies, list):
        return cached_movies

    config = load_config()
    if not is_configured(config):
        return []

    try:
        movies = _fetch_radarr_movies(config)
        _CACHE["movies"] = movies
        return movies
    except (requests.RequestException, ValueError) as exc:  # pragma: no cover - network errors
        print(f"Error fetching movies from Radarr: {exc}")
        return []


def _fetch_radarr_movies(config: Dict) -> List[Dict]:
    """Return the full list of movies from Radarr sorted alphabetically."""

    response = requests.get(
        f"{config['radarr_url']}/api/v3/movie",
        headers={"X-Api-Key": config["radarr_api_key"]},
        timeout=10,
    )
    response.raise_for_status()
    movies = response.json()
    if not isinstance(movies, list):
        raise ValueError("Radarr returned an invalid movie list.")
    movies.sort(key=lambda movie: str(movie.get("title", "")).lower())
    return movies


def _radarr_headers(config: Dict) -> Dict[str, str]:
    """Return request headers required for Radarr API calls."""

    return {"X-Api-Key": config["radarr_api_key"]}


def _radarr_request(
    method: str,
    path: str,
    *,
    config: Optional[Dict] = None,
    params: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> requests.Response:
    """Execute a Radarr API request and raise for HTTP errors."""

    cfg = config or load_config()
    if not is_configured(cfg):
        raise RuntimeError("Radarr has not been configured.")

    url = f"{cfg['radarr_url']}{path}"
    headers = _radarr_headers(cfg)
    if payload is not None:
        headers["Content-Type"] = "application/json"

    response = requests.request(
        method.upper(),
        url,
        headers=headers,
        params=params,
        json=payload,
        timeout=10,
    )
    response.raise_for_status()
    return response


def _lookup_tmdb_movie(tmdb_id: str, config: Dict) -> Optional[Dict]:
    """Return Radarr lookup data for a TMDb identifier."""

    if not tmdb_id:
        return None

    response = _radarr_request(
        "GET",
        "/api/v3/movie/lookup/tmdb",
        config=config,
        params={"tmdbId": tmdb_id},
    )

    try:
        payload = response.json()
    except ValueError:
        return None

    if isinstance(payload, list):
        return payload[0] if payload else None
    if isinstance(payload, dict):
        return payload
    return None


def _load_radarr_library_options(config: Dict) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Fetch Radarr root folders and quality profiles."""

    root_response = _radarr_request("GET", "/api/v3/rootFolder", config=config)
    quality_response = _radarr_request("GET", "/api/v3/qualityProfile", config=config)

    try:
        root_payload = root_response.json()
    except ValueError:
        root_payload = []
    try:
        quality_payload = quality_response.json()
    except ValueError:
        quality_payload = []

    root_folders: List[Dict[str, Any]] = []
    for entry in root_payload or []:
        if isinstance(entry, dict):
            root_folders.append(entry)

    quality_profiles: List[Dict[str, Any]] = []
    for entry in quality_payload or []:
        if isinstance(entry, dict):
            quality_profiles.append(entry)

    return root_folders, quality_profiles


def _select_default_root_path(root_folders: List[Dict[str, Any]]) -> Optional[str]:
    """Choose the default Radarr root folder path."""

    candidates: List[Dict[str, Any]] = []
    for entry in root_folders or []:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "").strip()
        if not path:
            continue
        candidates.append({"path": path, "accessible": bool(entry.get("accessible", True))})

    if not candidates:
        return None

    for entry in candidates:
        if entry.get("accessible", True):
            return entry["path"]

    return candidates[0]["path"]


def _select_default_quality_profile_id(quality_profiles: List[Dict[str, Any]]) -> Optional[int]:
    """Choose the default Radarr quality profile identifier."""

    for entry in quality_profiles or []:
        if not isinstance(entry, dict):
            continue
        try:
            profile_id = int(entry.get("id"))
        except (TypeError, ValueError):
            continue
        if profile_id >= 0:
            return profile_id
    return None


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
            match_title = match.get("title") or ""
            if year:
                description = (
                    f"Matched title '{title}' ({year}) to Radarr movie '{match_title}'."
                )
            else:
                description = f"Matched title '{title}' to Radarr movie '{match_title}'."
            log(description)
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


EXTRA_TYPE_ALIASES = {
    "trailers": "trailer",
    "behindthescene": "behindthescenes",
    "behindthescenesclip": "behindthescenes",
    "behindthescenesfeature": "behindthescenes",
    "behindthescenesfeaturette": "behindthescenes",
    "deletedscene": "deleted",
    "deletedscenes": "deleted",
    "featurettes": "featurette",
    "interviews": "interview",
    "scenes": "scene",
    "shorts": "short",
    "extras": "other",
}


def normalize_extra_type_key(raw_value: str) -> Optional[str]:
    """Return a canonical extra type key for a user-provided value."""

    token = re.sub(r"[^a-z]", "", str(raw_value or "").lower())
    if not token:
        return None
    if token in EXTRA_TYPE_LABELS:
        return token
    return EXTRA_TYPE_ALIASES.get(token)


def _describe_job(payload: Dict) -> Dict:
    """Build presentation metadata for a job payload."""
    movie_label = (payload.get("movieName") or payload.get("title") or "").strip()
    standalone = bool(payload.get("standalone"))
    if not movie_label:
        movie_label = "Standalone Download" if standalone else "Selected Movie"
    if standalone and movie_label == "Standalone Download":
        override_title = (payload.get("title") or "").strip()
        if override_title:
            movie_label = override_title
    if not standalone and movie_label == "Standalone Download":
        movie_label = "Selected Movie"
    extra = bool(payload.get("extra"))
    extra_type = (payload.get("extraType") or "trailer").strip().lower()
    extra_name = (payload.get("extra_name") or "").strip()
    merge_playlist = bool(payload.get("merge_playlist"))
    playlist_mode = (
        payload.get("playlist_mode")
        or ("merge" if merge_playlist else "single")
    ).strip().lower()
    if playlist_mode == "merge":
        merge_playlist = True
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
    if merge_playlist:
        metadata.append("Playlist merged into single file")
    if standalone:
        metadata.append("Standalone download (outside Radarr)")
    return {"label": label or "Radarr Download", "subtitle": subtitle, "metadata": metadata}


ALLOWED_PLAYLIST_MODES = {"single", "merge"}


def _validate_request_urls(data: Dict, error: Callable[[str], None]) -> str:
    """Return the validated YouTube URL from the request payload."""

    yt_url = (data.get("yturl") or "").strip()
    if not yt_url:
        error("YouTube URL is required.")
    elif not re.search(r"(youtube\.com|youtu\.be)/", yt_url):
        error("Please provide a valid YouTube URL.")
    return yt_url


def _validate_movie_selection(data: Dict, error: Callable[[str], None]) -> str:
    """Ensure a movie has been chosen from the suggestions list."""

    movie_id = (data.get("movieId") or "").strip()
    if not movie_id:
        error("No movie selected. Please choose a movie from the suggestions list.")
    return movie_id


def _resolve_playlist_mode(data: Dict, error: Callable[[str], None]) -> str:
    """Return the requested playlist handling mode."""

    playlist_mode = (data.get("playlist_mode") or "single").strip().lower()
    if playlist_mode not in ALLOWED_PLAYLIST_MODES:
        error("Invalid playlist handling option selected.")
        playlist_mode = "single"
    return playlist_mode


def _resolve_extra_settings(
    data: Dict, error: Callable[[str], None]
) -> Tuple[bool, str, str]:
    """Determine the extra storage options for the request."""

    extra_requested = bool(data.get("extra"))
    extra_name = (data.get("extra_name") or "").strip()

    if extra_requested and not extra_name:
        error("Extra name is required when storing in a subfolder.")

    selected_extra_type = (data.get("extraType") or "trailer").strip().lower()

    return extra_requested, extra_name, selected_extra_type


def _prepare_create_payload(data: Dict, error: Callable[[str], None]) -> Dict:
    """Validate and sanitise the incoming create payload."""

    playlist_mode = _resolve_playlist_mode(data, error)

    standalone = bool(data.get("standalone"))

    extra_requested, extra_name, selected_extra_type = _resolve_extra_settings(
        data, error
    )

    if standalone:
        extra_requested = False
        extra_name = ""
        selected_extra_type = "other"

    if standalone:
        movie_id = (data.get("movieId") or "").strip()
    else:
        movie_id = _validate_movie_selection(data, error)

    return {
        "yturl": _validate_request_urls(data, error),
        "movieId": movie_id,
        "movieName": (data.get("movieName") or "").strip(),
        "title": (data.get("title") or "").strip(),
        "year": (data.get("year") or "").strip(),
        "tmdb": (data.get("tmdb") or "").strip(),
        "extra": extra_requested,
        "extraType": selected_extra_type,
        "extra_name": extra_name,
        "merge_playlist": playlist_mode == "merge",
        "playlist_mode": playlist_mode,
        "standalone": standalone,
    }


def _format_root_folder(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise a Radarr root folder entry for the UI."""

    path = str(entry.get("path") or "").strip()
    return {
        "id": entry.get("id"),
        "name": entry.get("name") or path or "Root Folder",
        "path": path,
        "accessible": bool(entry.get("accessible", True)),
        "freeSpace": entry.get("freeSpace"),
    }


def _format_quality_profile(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise a Radarr quality profile entry for the UI."""

    return {
        "id": entry.get("id"),
        "name": entry.get("name") or f"Profile {entry.get('id')}",
    }


@app.route("/youtube/search", methods=["GET"])
def youtube_search() -> Response:
    """Search for YouTube videos matching the supplied query."""

    query = (request.args.get("q") or "").strip()
    if len(query) < 2:
        return (
            jsonify({"error": "Please provide a search query with at least 2 characters."}),
            400,
        )

    limit_value = request.args.get("limit", default=10, type=int)
    if limit_value is None:
        limit_value = 10

    try:
        results = _search_youtube(query, limit=limit_value)
    except RuntimeError as exc:
        app.logger.error("Failed to search YouTube for query %r: %s", query, exc)
        return jsonify({"error": "Failed to search YouTube."}), 502

    return jsonify({"results": results})


@app.route("/radarr/options", methods=["GET"])
def radarr_options():
    """Return Radarr library options required for quick movie creation."""

    config = load_config()
    if not is_configured(config):
        return jsonify({"error": "Application has not been configured yet."}), 503

    try:
        root_folders, quality_profiles = _load_radarr_library_options(config)
    except requests.HTTPError as exc:  # pragma: no cover - depends on Radarr
        response = exc.response
        status = response.status_code if response is not None else 502
        message = "Failed to load Radarr options."
        if response is not None:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                message = payload.get("message") or payload.get("error") or message
        return jsonify({"error": message}), status
    except (requests.RequestException, ValueError) as exc:  # pragma: no cover - network errors
        return jsonify({"error": f"Failed to load Radarr options: {exc}"}), 502

    formatted_roots = []
    for entry in root_folders or []:
        if isinstance(entry, dict):
            formatted_roots.append(_format_root_folder(entry))

    formatted_profiles = []
    for entry in quality_profiles or []:
        if isinstance(entry, dict):
            formatted_profiles.append(_format_quality_profile(entry))

    return jsonify(
        {
            "rootFolders": formatted_roots,
            "qualityProfiles": formatted_profiles,
        }
    )


class RadarrRequestError(Exception):
    """Raised when a Radarr integration call cannot be completed."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True)
class RadarrMovieOptions:
    """Configuration details required to create a Radarr movie."""

    root_folder_path: str
    quality_profile_id: int
    monitored: bool
    search: bool


def _json_error(message: str, status: int) -> Tuple[Response, int]:
    """Return a JSON error payload with the provided HTTP status."""

    return jsonify({"error": message}), status


def _require_configured() -> Dict:
    """Ensure the application is configured before performing Radarr calls."""

    config = load_config()
    if not is_configured(config):
        raise RadarrRequestError("Application has not been configured yet.", 503)
    return config


def _extract_radarr_error(response: Optional[requests.Response], default: str) -> str:
    """Parse a Radarr error response for a useful message."""

    if response is None:
        return default
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        return payload.get("message") or payload.get("error") or default
    return default


def _raise_radarr_http_error(exc: requests.HTTPError, default: str) -> None:
    """Convert a Radarr HTTP error into a RadarrRequestError."""

    response = exc.response
    status = response.status_code if response is not None else 502
    message = _extract_radarr_error(response, default)
    raise RadarrRequestError(message, status) from exc


def _parse_tmdb_id(data: Dict[str, Any]) -> str:
    """Extract and validate the TMDb identifier from the request payload."""

    tmdb_id = str(data.get("tmdbId") or "").strip()
    if not tmdb_id or not tmdb_id.isdigit():
        message = "TMDb ID is required." if not tmdb_id else "TMDb ID must be numeric."
        raise RadarrRequestError(message, 400)
    return tmdb_id


def _resolve_library_selection(
    data: Dict[str, Any], config: Dict
) -> Tuple[str, int, bool, bool]:
    """Resolve root folder, quality profile, monitoring, and search defaults."""

    root_folder_path = str(data.get("rootFolderPath") or "").strip()
    quality_profile_id = _extract_quality_profile_id(data.get("qualityProfileId"))
    monitored = bool(data.get("monitored", True))
    search_flag = data.get("search")

    needs_defaults = not root_folder_path or quality_profile_id is None
    if needs_defaults:
        root_folder_path, quality_profile_id = _load_default_library_options(
            root_folder_path, quality_profile_id, config
        )

    if not root_folder_path:
        raise RadarrRequestError(
            "Radarr does not have any root folders configured.", 503
        )
    if quality_profile_id is None:
        raise RadarrRequestError(
            "Radarr does not have any quality profiles configured.", 503
        )

    search = bool(search_flag) if search_flag is not None else needs_defaults

    return root_folder_path, int(quality_profile_id), monitored, search


def _load_default_library_options(
    root_folder_path: str, quality_profile_id: Optional[int], config: Dict
) -> Tuple[str, int]:
    """Fetch Radarr library options and select sensible defaults."""

    try:
        root_folders, quality_profiles = _load_radarr_library_options(config)
    except requests.HTTPError as exc:  # pragma: no cover - depends on Radarr
        _raise_radarr_http_error(exc, "Failed to load Radarr library options.")
    except (requests.RequestException, ValueError) as exc:  # pragma: no cover - network errors
        raise RadarrRequestError(f"Failed to load Radarr options: {exc}", 502) from exc

    resolved_root = root_folder_path or _select_default_root_path(root_folders) or ""
    resolved_profile = (
        quality_profile_id
        if quality_profile_id is not None
        else _select_default_quality_profile_id(quality_profiles)
    )

    if not resolved_root:
        raise RadarrRequestError(
            "Radarr does not have any root folders configured.", 503
        )
    if resolved_profile is None:
        raise RadarrRequestError(
            "Radarr does not have any quality profiles configured.", 503
        )

    return resolved_root, int(resolved_profile)


def _fetch_movie_lookup(tmdb_id: str, config: Dict) -> Dict[str, Any]:
    """Fetch movie lookup data from Radarr, raising helpful errors when unavailable."""

    try:
        lookup = _lookup_tmdb_movie(tmdb_id, config)
    except requests.HTTPError as exc:  # pragma: no cover - depends on Radarr
        _raise_radarr_http_error(exc, "Failed to query Radarr for lookup data.")
    except requests.RequestException as exc:  # pragma: no cover - network errors
        raise RadarrRequestError(f"Failed to query Radarr: {exc}", 502) from exc

    if not lookup:
        raise RadarrRequestError("Movie not found.", 404)

    return lookup


def _search_radarr_movies(query: str, config: Dict) -> List[Dict[str, Any]]:
    """Run a Radarr lookup query and return the resulting payload."""

    try:
        response = _radarr_request(
            "GET",
            "/api/v3/movie/lookup",
            config=config,
            params={"term": query},
        )
    except requests.HTTPError as exc:  # pragma: no cover - depends on Radarr
        _raise_radarr_http_error(exc, "Failed to search Radarr for movies.")
    except requests.RequestException as exc:  # pragma: no cover - network errors
        raise RadarrRequestError(f"Failed to search Radarr: {exc}", 502) from exc

    try:
        payload = response.json()
    except ValueError as exc:  # pragma: no cover - invalid JSON
        raise RadarrRequestError(f"Failed to search Radarr: {exc}", 502) from exc

    return payload if isinstance(payload, list) else []


def _create_radarr_movie(payload: Dict[str, Any], config: Dict) -> Dict[str, Any]:
    """Send a Radarr movie creation request and return the created record."""

    try:
        response = _radarr_request(
            "POST", "/api/v3/movie", config=config, payload=payload
        )
    except requests.HTTPError as exc:  # pragma: no cover - depends on Radarr
        _raise_radarr_http_error(exc, "Radarr rejected the movie creation request.")
    except requests.RequestException as exc:  # pragma: no cover - network errors
        raise RadarrRequestError(f"Failed to add movie to Radarr: {exc}", 502) from exc

    try:
        return response.json()
    except ValueError as exc:  # pragma: no cover - invalid JSON
        raise RadarrRequestError(f"Failed to add movie to Radarr: {exc}", 502) from exc


def _build_movie_creation_payload(
    lookup: Dict[str, Any], tmdb_id: str, options: RadarrMovieOptions
) -> Dict[str, Any]:
    """Build the payload Radarr expects when creating a movie."""

    images = lookup.get("images") if isinstance(lookup.get("images"), list) else []
    tags = lookup.get("tags") if isinstance(lookup.get("tags"), list) else []

    return {
        "title": lookup.get("title")
        or lookup.get("originalTitle")
        or lookup.get("sortTitle")
        or "Untitled",
        "qualityProfileId": options.quality_profile_id,
        "titleSlug": lookup.get("titleSlug") or str(tmdb_id),
        "tmdbId": int(lookup.get("tmdbId") or tmdb_id),
        "year": lookup.get("year"),
        "images": images,
        "rootFolderPath": options.root_folder_path,
        "monitored": options.monitored,
        "minimumAvailability": lookup.get("minimumAvailability") or "released",
        "addOptions": {"searchForMovie": bool(options.search)},
        "tags": tags,
    }


@app.route("/radarr/search", methods=["GET"])
def radarr_search():
    """Search Radarr for movies using a text query."""

    query = (request.args.get("query") or "").strip()
    if len(query) < 2:
        message = (
            "Search query is required."
            if not query
            else "Search query must be at least 2 characters."
        )
        return _json_error(message, 400)

    try:
        config = _require_configured()
        payload = _search_radarr_movies(query, config)
    except RadarrRequestError as exc:
        return _json_error(exc.message, exc.status)

    results: List[Dict[str, Any]] = []
    for entry in payload or []:
        if not isinstance(entry, dict):
            continue
        tmdb_id = entry.get("tmdbId")
        if tmdb_id is None:
            continue
        preview = _build_lookup_preview(entry, str(tmdb_id))
        if preview.get("tmdbId"):
            results.append(preview)

    return jsonify({"results": results})


def _build_lookup_preview(lookup: Dict[str, Any], tmdb_id: str) -> Dict[str, Any]:
    """Transform Radarr lookup data into a preview payload."""

    genres = lookup.get("genres") if isinstance(lookup.get("genres"), list) else []
    return {
        "title": lookup.get("title")
        or lookup.get("originalTitle")
        or lookup.get("sortTitle")
        or "",
        "year": lookup.get("year"),
        "tmdbId": lookup.get("tmdbId") or tmdb_id,
        "overview": lookup.get("overview") or "",
        "runtime": lookup.get("runtime"),
        "genres": genres,
        "remotePoster": lookup.get("remotePoster") or "",
        "images": lookup.get("images") if isinstance(lookup.get("images"), list) else [],
        "titleSlug": lookup.get("titleSlug") or "",
        "minimumAvailability": lookup.get("minimumAvailability") or "released",
    }


@app.route("/radarr/lookup", methods=["GET"])
def radarr_lookup():
    """Return preview details for a TMDb movie via Radarr."""

    tmdb_id = (request.args.get("tmdbId") or "").strip()
    if not tmdb_id or not tmdb_id.isdigit():
        message = "TMDb ID is required." if not tmdb_id else "TMDb ID must be numeric."
        return _json_error(message, 400)

    try:
        config = _require_configured()
        lookup = _fetch_movie_lookup(tmdb_id, config)
    except RadarrRequestError as exc:
        return _json_error(exc.message, exc.status)

    return jsonify({"movie": _build_lookup_preview(lookup, tmdb_id)})


def _extract_quality_profile_id(raw: Any) -> Optional[int]:
    """Convert incoming profile identifiers into integers."""

    try:
        if isinstance(raw, bool):
            return None
        return int(raw)
    except (TypeError, ValueError):
        return None


@app.route("/radarr/movies/refresh", methods=["POST"])
def radarr_refresh_movies() -> Response:
    """Force refresh the cached Radarr movie library."""

    try:
        config = _require_configured()
    except RadarrRequestError as exc:
        return _json_error(exc.message, exc.status)

    try:
        movies = _fetch_radarr_movies(config)
        _CACHE["movies"] = movies
    except (requests.RequestException, ValueError) as exc:  # pragma: no cover - network errors
        print(f"Error refreshing movies from Radarr: {exc}")
        return _json_error("Failed to refresh Radarr movies.", 502)

    payload: List[Dict[str, Any]] = []
    for movie in movies:
        if isinstance(movie, dict):
            payload.append(
                {
                    "id": movie.get("id"),
                    "title": movie.get("title"),
                    "year": movie.get("year"),
                    "tmdbId": movie.get("tmdbId"),
                }
            )

    return jsonify({"movies": payload})


@app.route("/radarr/movies", methods=["POST"])
def radarr_add_movie():
    """Create a new movie in Radarr from TMDb lookup data."""

    data = request.get_json(silent=True) or {}
    try:
        config = _require_configured()
        tmdb_id = _parse_tmdb_id(data)
        root_folder_path, quality_profile_id, monitored, search = _resolve_library_selection(
            data, config
        )
        options = RadarrMovieOptions(
            root_folder_path=root_folder_path,
            quality_profile_id=quality_profile_id,
            monitored=monitored,
            search=search,
        )
        lookup = _fetch_movie_lookup(tmdb_id, config)
        payload = _build_movie_creation_payload(lookup, tmdb_id, options)
        created = _create_radarr_movie(payload, config)
    except RadarrRequestError as exc:
        return _json_error(exc.message, exc.status)

    _CACHE["movies"] = None

    return jsonify(
        {
            "movie": {
                "id": created.get("id"),
                "title": created.get("title"),
                "year": created.get("year"),
                "tmdbId": created.get("tmdbId"),
            }
        }
    )


@app.route("/", methods=["GET"])
def index():
    """Render the main application interface."""
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
    """Create a new download job from the submitted request payload."""
    config = load_config()
    if not is_configured(config):
        return jsonify({"logs": ["ERROR: Application has not been configured yet."]}), 503

    data = request.get_json(silent=True) or {}
    logs: List[str] = []
    errors: List[str] = []

    def error(message: str) -> None:
        logs.append(f"ERROR: {message}")
        errors.append(message)

    payload = _prepare_create_payload(data, error)

    if errors:
        return jsonify({"logs": logs}), 400

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

    cancel_event = threading.Event()
    worker = threading.Thread(
        target=process_download_job,
        args=(job_id, payload, cancel_event),
        daemon=True,
    )
    _register_job_control(job_id, worker, cancel_event)
    worker.start()

    display_job = dict(job_record)
    display_job["logs"] = _filter_logs_for_display(
        display_job.get("logs", []), config.get("debug_mode", False)
    )

    return jsonify({"job": display_job, "debug_mode": config.get("debug_mode", False)}), 202


def process_download_job(
    job_id: str, payload: Dict, cancel_event: threading.Event
) -> None:
    """Execute the yt-dlp workflow for a queued job."""
    # pylint: disable=too-many-locals,too-many-branches,too-many-nested-blocks
    # pylint: disable=too-many-statements,too-many-return-statements
    def log(message: str) -> None:
        append_job_log(job_id, message)

    def warn(message: str) -> None:
        append_job_log(job_id, f"WARNING: {message}")

    def fail(message: str) -> None:
        append_job_log(job_id, f"ERROR: {message}")
        _mark_job_failure(job_id, message)

    def debug(message: str) -> None:
        append_job_log(job_id, f"DEBUG: {message}")

    cancellation_logged = False
    playlist_temp_dir: Optional[str] = None
    expected_pattern: Optional[str] = None
    downloaded_candidates: List[str] = []
    merge_playlist = False

    def acknowledge_cancellation() -> None:
        nonlocal cancellation_logged
        if not cancellation_logged:
            append_job_log(job_id, "Cancellation acknowledged; stopping job.")
            cancellation_logged = True

    def ensure_not_cancelled() -> None:
        if cancel_event.is_set():
            acknowledge_cancellation()
            raise JobCancelled()

    try:
        _job_status(job_id, "processing", progress=1)
        config = load_config()
        if not is_configured(config):
            fail("Application has not been configured yet.")
            return

        ensure_not_cancelled()

        debug_enabled = bool(config.get("debug_mode"))
        compact_progress_logs = not debug_enabled
        cookie_path = get_cookie_path(config)

        yt_url = (payload.get("yturl") or "").strip()
        movie_id = (payload.get("movieId") or "").strip()
        tmdb = (payload.get("tmdb") or "").strip()
        title = (payload.get("title") or "").strip()
        year = (payload.get("year") or "").strip()
        merge_playlist = bool(payload.get("merge_playlist"))
        playlist_mode = (
            payload.get("playlist_mode")
            or ("merge" if merge_playlist else "single")
        ).strip().lower()
        if playlist_mode not in ALLOWED_PLAYLIST_MODES:
            warn(f"Invalid playlist mode '{playlist_mode}', defaulting to single video.")
            playlist_mode = "single"
        merge_playlist = playlist_mode == "merge"
        payload["playlist_mode"] = playlist_mode
        payload["merge_playlist"] = merge_playlist

        standalone = bool(payload.get("standalone"))
        payload["standalone"] = standalone

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

        ensure_not_cancelled()

        extra = bool(payload.get("extra")) and not standalone
        extra_name = (payload.get("extra_name") or "").strip() if extra else ""
        payload["extra"] = extra
        payload["extra_name"] = extra_name
        jobs_repo.update(job_id, {"request": payload})

        movie: Dict[str, Any] = {}
        target_dir = ""
        canonical_stem = ""
        standalone_base_path: Optional[str] = None
        download_dir: Optional[str] = None

        if standalone:
            standalone_base_path = _select_standalone_library_path(config)
            if standalone_base_path is None:
                fail("Standalone downloads require at least one accessible library path.")
                return
            log("Standalone download requested; skipping Radarr library lookup.")
            log(f"Standalone base path resolved to '{standalone_base_path}'.")
            target_dir = standalone_base_path
            _job_status(job_id, "processing", progress=10)
        else:
            resolved = resolve_movie_by_metadata(movie_id, tmdb, title, year, log)
            if resolved is None or not str(resolved.get("id")):
                fail("No movie selected. Please choose a movie from the suggestions list.")
                return
            movie_id = str(resolved.get("id"))
            payload["movieId"] = movie_id
            jobs_repo.update(job_id, {"request": payload})

            try:
                log(f"Fetching Radarr details for movie ID {movie_id}.")
                response = requests.get(
                    f"{config['radarr_url']}/api/v3/movie/{movie_id}",
                    headers={"X-Api-Key": config["radarr_api_key"]},
                    timeout=10,
                )
                response.raise_for_status()
                movie = response.json()
            except (requests.RequestException, ValueError) as exc:
                # pragma: no cover - network errors
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

            ensure_not_cancelled()

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
            if extra:
                extra_label = sanitize_filename(extra_name) or EXTRA_TYPE_LABELS.get(
                    extra_type, extra_type.capitalize()
                )
                if extra_label:
                    canonical_stem = f"{movie_stem} {extra_label}"
                    log(f"Using extra label '{extra_label}'.")

            download_dir = target_dir

        if merge_playlist:
            log("Playlist download requested; videos will be merged into a single file.")

        descriptive = extra_name if extra else ""
        default_label = "Playlist" if merge_playlist else "Video"
        if descriptive:
            log(f"Using custom descriptive name '{descriptive}'.")

        info_payload: Optional[Dict] = None

        if shutil.which("ffmpeg") is None:
            warn(
                "ffmpeg executable not found; yt-dlp may fall back to a lower quality "
                "progressive stream."
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
        ]
        if merge_playlist:
            info_command.append("--yes-playlist")
        else:
            info_command.append("--no-playlist")
        info_command += [
            "--print-json",
            yt_url,
        ]

        resolved_format: Dict[str, str] = {}
        ensure_not_cancelled()

        info_payload = None
        info_stdout = ""
        info_stderr = ""
        info_returncode: Optional[int] = None
        metadata_timed_out = False

        log("Fetching YouTube metadata to determine output naming and formats.")

        try:
            with subprocess.Popen(
                info_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=False,
            ) as info_process:
                _set_job_process(job_id, info_process)
                start_time = time.monotonic()

                selector = selectors.DefaultSelector()
                stdout_chunks: List[bytes] = []
                stderr_chunks: List[bytes] = []

                if info_process.stdout is not None:
                    selector.register(info_process.stdout, selectors.EVENT_READ, "stdout")
                if info_process.stderr is not None:
                    selector.register(info_process.stderr, selectors.EVENT_READ, "stderr")

                def _drain_events(timeout: float) -> None:
                    try:
                        events = selector.select(timeout=timeout)
                    except OSError:
                        events = []
                    for key, _ in events:
                        stream = key.fileobj
                        label = key.data
                        try:
                            chunk = stream.read1(4096)
                        except (ValueError, OSError):
                            chunk = b""
                        if not chunk:
                            try:
                                selector.unregister(stream)
                            except (KeyError, ValueError):
                                pass
                            try:
                                stream.close()
                            except OSError:
                                pass
                            continue
                        if label == "stdout":
                            stdout_chunks.append(chunk)
                        else:
                            stderr_chunks.append(chunk)

                try:
                    while True:
                        if cancel_event.is_set():
                            acknowledge_cancellation()
                            _terminate_process(info_process)
                            raise JobCancelled()

                        if METADATA_FETCH_TIMEOUT_SECONDS:
                            elapsed = time.monotonic() - start_time
                            if elapsed >= METADATA_FETCH_TIMEOUT_SECONDS:
                                metadata_timed_out = True
                                warn(
                                    (
                                        "yt-dlp metadata query exceeded %s seconds; "
                                        "continuing without metadata."
                                    )
                                    % METADATA_FETCH_TIMEOUT_SECONDS
                                )
                                _terminate_process(info_process)
                                break

                        _drain_events(timeout=0.2)

                        if not selector.get_map():
                            if info_process.poll() is not None:
                                break
                            try:
                                info_process.wait(timeout=0.2)
                            except subprocess.TimeoutExpired:
                                continue
                            else:
                                break

                        if info_process.poll() is not None:
                            # Process has exited but there may still be buffered data.
                            _drain_events(timeout=0)
                            if not selector.get_map():
                                break
                finally:
                    # Drain any remaining buffered data without blocking.
                    try:
                        _drain_events(timeout=0)
                    except (OSError, ValueError, RuntimeError):
                        # pragma: no cover - defensive cleanup
                        pass
                    selector.close()

                try:
                    info_returncode = info_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _terminate_process(info_process)
                    try:
                        info_returncode = info_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        info_returncode = info_process.returncode
                except OSError:
                    info_returncode = info_process.returncode

                info_stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
                info_stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        except (
            FileNotFoundError,
            OSError,
            ValueError,
        ) as exc:  # pragma: no cover - command failure
            warn(f"Failed to query format details via yt-dlp: {exc}")
        finally:
            _clear_job_process(job_id)

        if cancel_event.is_set():
            acknowledge_cancellation()
            raise JobCancelled()

        if info_returncode not in (0, None) and not metadata_timed_out:
            warn(
                "yt-dlp metadata query exited with status "
                f"{info_returncode}; continuing without metadata."
            )
        else:
            info_entries: List[Dict[str, Any]] = []
            for raw_line in info_stdout.splitlines():
                stripped = raw_line.strip()
                if not stripped:
                    continue
                try:
                    parsed_line = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed_line, dict):
                    info_entries.append(parsed_line)

            if not info_entries and info_stdout.strip():
                try:
                    parsed_blob = json.loads(info_stdout)
                except json.JSONDecodeError:
                    parsed_blob = None
                if isinstance(parsed_blob, dict):
                    info_entries.append(parsed_blob)

            preferred_entry: Optional[Dict[str, Any]] = None
            for candidate in reversed(info_entries):
                entry_type = str(candidate.get("_type") or "video").lower()
                if entry_type in {"playlist", "multi_video", "multi"}:
                    continue
                preferred_entry = candidate
                break

            if preferred_entry is None and info_entries:
                preferred_entry = info_entries[-1]

            info_payload = preferred_entry

            if info_entries and debug_enabled:
                debug(
                    "yt-dlp metadata candidates: "
                    + ", ".join(
                        str(entry.get("_type") or "video") for entry in info_entries
                    )
                )

            if info_stderr:
                for line in info_stderr.strip().splitlines():
                    debug(f"yt-dlp metadata: {line}")

            if info_payload:
                log("YouTube metadata retrieved successfully.")

            if info_payload:
                resolved_format = _resolve_requested_format(info_payload)

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

        if not descriptive:
            candidate_title = ""
            if info_payload:
                if merge_playlist:
                    candidate_title = (
                        info_payload.get("playlist_title")
                        or info_payload.get("playlist")
                        or info_payload.get("title")
                        or ""
                    )
                else:
                    candidate_title = info_payload.get("title") or ""
            candidate_title = candidate_title.strip()
            if candidate_title:
                descriptive = candidate_title
                log(f"Using YouTube title '{descriptive}'.")
            else:
                descriptive = default_label
                subject = "playlist" if merge_playlist else "video"
                warn(
                    f"yt-dlp did not provide a {subject} title. Using fallback name "
                    f"'{default_label}'."
                )

        descriptive = sanitize_filename(descriptive) or default_label

        if extra:
            extra_suffix = sanitize_filename(extra_name) or extra_type
            if extra_suffix:
                filename_base = f"{descriptive}-{extra_suffix}"
            else:
                filename_base = descriptive
        else:
            filename_base = descriptive

        filename_base = filename_base or "Video"

        if standalone:
            standalone_folder_name = filename_base
            if info_payload:
                if merge_playlist:
                    standalone_folder_name = (
                        info_payload.get("playlist_title")
                        or info_payload.get("playlist")
                        or info_payload.get("title")
                        or standalone_folder_name
                    )
                else:
                    standalone_folder_name = (
                        info_payload.get("title") or standalone_folder_name
                    )

            standalone_folder_name = sanitize_filename(
                (standalone_folder_name or "").strip()
            ) or filename_base or "Video"

            if not canonical_stem:
                canonical_stem = standalone_folder_name

            if standalone_base_path is None:
                fail("Standalone downloads require a configured library path.")
                return

            final_folder_path = os.path.join(
                standalone_base_path, standalone_folder_name
            )
            created_new = False
            if os.path.isfile(final_folder_path):
                suffix = 1
                base_name = standalone_folder_name
                while True:
                    candidate_name = f"{base_name} ({suffix})"
                    candidate_path = os.path.join(
                        standalone_base_path, candidate_name
                    )
                    if not os.path.exists(candidate_path) or os.path.isdir(candidate_path):
                        final_folder_path = candidate_path
                        standalone_folder_name = candidate_name
                        break
                    suffix += 1

            if not os.path.isdir(final_folder_path):
                try:
                    os.makedirs(final_folder_path, exist_ok=True)
                except OSError as exc:
                    fail(f"Failed to create standalone folder '{final_folder_path}': {exc}")
                    return
                created_new = True

            if created_new:
                log(f"Created standalone folder at '{final_folder_path}'.")
            else:
                log(f"Standalone folder resolved to '{final_folder_path}'.")

            target_dir = final_folder_path
            download_dir = final_folder_path
            filename_base = standalone_folder_name
            canonical_stem = standalone_folder_name

        if download_dir is None:
            fail("Internal error: download directory could not be determined.")
            return

        download_filename_base = filename_base

        pattern = os.path.join(download_dir, f"{download_filename_base}.*")
        if any(os.path.exists(path) for path in glob_paths(pattern)):
            log(
                f"File stem '{download_filename_base}' already exists. "
                "Searching for a free filename."
            )
            suffix_index = 1
            while True:
                candidate_base = f"{download_filename_base} ({suffix_index})"
                candidate_pattern = os.path.join(download_dir, f"{candidate_base}.*")
                if not any(os.path.exists(path) for path in glob_paths(candidate_pattern)):
                    download_filename_base = candidate_base
                    log(f"Selected new filename stem '{download_filename_base}'.")
                    break
                suffix_index += 1

        template_base = download_filename_base.replace("%", "%%")
        if merge_playlist:
            playlist_temp_dir = os.path.join(download_dir, f".yt2radarr_playlist_{job_id}")
            os.makedirs(playlist_temp_dir, exist_ok=True)
            log(
                "Playlist merge enabled. Downloads will be staged in "
                f"'{os.path.basename(playlist_temp_dir)}'."
            )
            target_template = os.path.join(
                playlist_temp_dir, "%(playlist_index)05d - %(title)s.%(ext)s"
            )
            expected_pattern = os.path.join(playlist_temp_dir, "*.*")
        else:
            target_template = os.path.join(download_dir, f"{template_base}.%(ext)s")
            expected_pattern = os.path.join(download_dir, f"{download_filename_base}.*")

        command = ["yt-dlp"]
        if cookie_path:
            command += ["--cookies", cookie_path]
        command += ["--newline"]
        command += ["-f", format_selector]
        if merge_playlist:
            command.append("--yes-playlist")
        else:
            command.append("--no-playlist")
        command += ["-o", target_template, yt_url]

        log("Running yt-dlp with explicit output template.")

        _job_status(job_id, "processing", progress=20)

        output_lines: List[str] = []
        progress_log_active = False

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

        ensure_not_cancelled()

        return_code = 0
        try:
            with subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=download_dir,
                stdin=subprocess.DEVNULL,
            ) as process:
                _set_job_process(job_id, process)
                assert process.stdout is not None
                for raw_line in process.stdout:
                    if cancel_event.is_set():
                        acknowledge_cancellation()
                        try:
                            process.terminate()
                        except OSError:
                            pass
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            try:
                                process.kill()
                            except OSError:
                                pass
                        _cleanup_temp_files(expected_pattern)
                        raise JobCancelled()
                    line = raw_line.rstrip()
                    if not line:
                        continue
                    handle_output_line(line)
                return_code = process.wait()
        except (OSError, ValueError) as exc:  # pragma: no cover - command failure
            fail(f"Failed to invoke yt-dlp: {exc}")
            return
        finally:
            _clear_job_process(job_id)

        if return_code != 0:
            failure_summary = output_lines[-1] if output_lines else "Download failed."
            log(f"yt-dlp exited with code {return_code}.")

            _cleanup_temp_files(expected_pattern)

            fail(f"Download failed: {failure_summary[:300]}")
            return

        downloaded_candidates = [
            path
            for path in glob_paths(expected_pattern)
            if os.path.isfile(path) and not path.endswith((".part", ".ytdl"))
        ]

        if cancel_event.is_set():
            acknowledge_cancellation()
            for candidate in list(downloaded_candidates):
                try:
                    os.remove(candidate)
                except OSError:
                    continue
            _cleanup_temp_files(expected_pattern)
            raise JobCancelled()

        def _is_intermediate_file(name: str) -> bool:
            base = os.path.basename(name)
            if base.endswith(".temp") or ".temp." in base:
                return True
            return bool(re.search(r"\.f\d+\.\w+$", base))

        if not downloaded_candidates:
            fail("Download completed but the output file could not be located.")
            return

        if merge_playlist:
            if not playlist_temp_dir:
                fail("Internal error: playlist staging directory was not created.")
                return
            ffmpeg_path = shutil.which("ffmpeg")
            if ffmpeg_path is None:
                fail("ffmpeg is required to merge playlist videos but was not found.")
                return
            downloaded_candidates.sort()
            segment_count = len(downloaded_candidates)
            log(
                f"Merging playlist videos with ffmpeg (segments: {segment_count})."
            )

            def _escape_concat_path(value: str) -> str:
                return value.replace("\\", "\\\\").replace("'", "\\'")

            concat_manifest = os.path.join(playlist_temp_dir, "concat.txt")
            ensure_not_cancelled()
            try:
                with open(concat_manifest, "w", encoding="utf-8") as handle:
                    for candidate in downloaded_candidates:
                        handle.write(
                            f"file '{_escape_concat_path(os.path.abspath(candidate))}'\n"
                        )
            except OSError as exc:
                fail(f"Failed to prepare playlist merge manifest: {exc}")
                return

            first_ext = os.path.splitext(downloaded_candidates[0])[1] or ".mp4"
            merged_output_path = os.path.join(playlist_temp_dir, f"merged{first_ext}")

            merge_command = [
                ffmpeg_path,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                concat_manifest,
                "-c",
                "copy",
                merged_output_path,
            ]

            ensure_not_cancelled()

            stdout_data = ""
            stderr_data = ""
            merge_returncode = 0
            try:
                with subprocess.Popen(
                    merge_command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    stdin=subprocess.DEVNULL,
                ) as merge_process:
                    _set_job_process(job_id, merge_process)
                    try:
                        stdout_data, stderr_data = merge_process.communicate()
                    finally:
                        _clear_job_process(job_id)
                    merge_returncode = merge_process.returncode
            except (OSError, ValueError) as exc:
                fail(f"Failed to invoke ffmpeg for playlist merge: {exc}")
                return

            if stdout_data:
                for line in stdout_data.strip().splitlines():
                    debug(f"ffmpeg: {line}")
            if stderr_data:
                for line in stderr_data.strip().splitlines():
                    debug(f"ffmpeg: {line}")

            if cancel_event.is_set():
                acknowledge_cancellation()
                if os.path.exists(merged_output_path):
                    try:
                        os.remove(merged_output_path)
                    except OSError:
                        pass
                raise JobCancelled()

            if merge_returncode != 0 or not os.path.exists(merged_output_path):
                fail("Failed to merge playlist videos into a single file.")
                return

            log("Merging playlist videos completed successfully.")

            try:
                os.remove(concat_manifest)
            except OSError:
                pass

            for candidate in downloaded_candidates:
                if os.path.abspath(candidate) == os.path.abspath(merged_output_path):
                    continue
                try:
                    os.remove(candidate)
                except OSError:
                    continue

            downloaded_candidates = [merged_output_path]

            if cancel_event.is_set():
                acknowledge_cancellation()
                for candidate in list(downloaded_candidates):
                    try:
                        os.remove(candidate)
                    except OSError:
                        continue
                raise JobCancelled()


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
                (
                    f"Canonical filename '{canonical_filename}' already exists. "
                    "Searching for a free name."
                )
            )
            name_suffix = 1
            while True:
                new_filename = f"{base_name} ({name_suffix}){ext_part}"
                candidate = os.path.join(target_dir, new_filename)
                if not os.path.exists(candidate):
                    canonical_filename = new_filename
                    canonical_path = candidate
                    log(f"Selected canonical filename '{new_filename}'.")
                    break
                name_suffix += 1

        try:
            if os.path.abspath(target_path) != os.path.abspath(canonical_path):
                log(
                    f"Renaming downloaded file to canonical name '{canonical_filename}'."
                )
                os.replace(target_path, canonical_path)
                target_path = canonical_path
            else:
                log("Download already matches canonical filename.")
        except OSError as exc:
            fail(
                f"Failed to rename downloaded file to '{canonical_filename}': {exc}"
            )
            return

        if cancel_event.is_set():
            acknowledge_cancellation()
            try:
                if os.path.exists(target_path):
                    os.remove(target_path)
            except OSError:
                pass
            raise JobCancelled()

        for leftover in downloaded_candidates:
            if os.path.abspath(leftover) == os.path.abspath(target_path):
                continue
            if not _is_intermediate_file(leftover):
                continue
            try:
                os.remove(leftover)
            except OSError:
                continue

        if merge_playlist and playlist_temp_dir:
            try:
                shutil.rmtree(playlist_temp_dir)
            except OSError:
                pass

        _job_status(job_id, "processing", progress=100)
        log(f"Success! Video saved as '{target_path}'.")
        _mark_job_success(job_id)
    except JobCancelled:
        _cleanup_temp_files(expected_pattern)
        for candidate in list(downloaded_candidates):
            try:
                os.remove(candidate)
            except OSError:
                continue
        _cleanup_playlist_dir(playlist_temp_dir)
        append_job_log(job_id, "Job cancelled.")
        _mark_job_cancelled(job_id)
    # pylint: disable=broad-exception-caught
    except Exception as exc:  # pragma: no cover - unexpected failure
        fail(f"Unexpected error: {exc}")
    # pylint: enable=broad-exception-caught
    finally:
        _clear_job_process(job_id)
        _unregister_job_control(job_id)


@app.route("/jobs/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id: str):
    """Request cancellation for an active job."""

    job = jobs_repo.get(job_id)
    if job is None:
        return jsonify({"error": "Job not found."}), 404

    status = str(job.get("status") or "").lower()
    if status not in {"queued", "processing"}:
        return (
            jsonify({"job": job, "message": "Job is not active and cannot be cancelled."}),
            409,
        )

    process_to_terminate: Optional[subprocess.Popen] = None
    already_requested = False
    with _JOB_CONTROLS_LOCK:
        control = _JOB_CONTROLS.get(job_id)
        if control is None:
            return (
                jsonify({"job": job, "message": "Job worker is no longer active."}),
                409,
            )
        already_requested = control.cancel_event.is_set()
        control.cancel_event.set()
        process_to_terminate = control.process

    if process_to_terminate is not None:
        _terminate_process(process_to_terminate)

    message = "Cancellation already requested." if already_requested else "Cancellation requested."

    if not already_requested:
        append_job_log(job_id, "Cancellation requested by user.")
        jobs_repo.update(job_id, {"message": "Cancelling..."})

    updated_job = jobs_repo.get(job_id) or job
    return jsonify({"job": updated_job, "message": message}), 202


@app.route("/jobs", methods=["GET"])
def jobs_index():
    """Return the current job list and debug mode flag."""
    config = load_config()
    return jsonify(
        {
            "jobs": jobs_repo.list(),
            "debug_mode": config.get("debug_mode", False),
        }
    )


@app.route("/jobs/<job_id>", methods=["GET"])
def job_detail(job_id: str):
    """Return detailed information for a specific job."""
    config = load_config()
    job = jobs_repo.get(job_id, include_logs=True)
    if job is None:
        return jsonify({"error": "Job not found."}), 404
    job["logs"] = _filter_logs_for_display(job.get("logs", []), config.get("debug_mode", False))
    return jsonify({"job": job, "debug_mode": config.get("debug_mode", False)})


def _resolve_override_target(
    normalized_original: str,
    overrides: Iterable[Dict[str, str]],
    ensure_candidate: Callable[[str, Optional[str]], Optional[str]],
) -> Optional[str]:
    """Return a resolved path using configured override mappings."""

    for override in overrides:
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
            return resolved

    return None


def _select_standalone_library_path(config: Dict) -> Optional[str]:
    """Return the first accessible library path for standalone downloads."""

    for entry in config.get("file_paths", []):
        candidate = str(entry or "").strip()
        if not candidate:
            continue
        if os.path.isdir(candidate):
            return candidate
    return None


def _resolve_library_target(
    folder_name: str,
    search_paths: Iterable[str],
    ensure_candidate: Callable[[str, Optional[str]], Optional[str]],
) -> Optional[str]:
    """Return a resolved path using configured library search paths."""

    for base_path in search_paths:
        candidate = os.path.join(base_path, folder_name)
        resolved = ensure_candidate(candidate, base_path)
        if resolved:
            return resolved
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

    if not original_path:
        return None, created

    normalized_path = os.path.normpath(str(original_path))
    resolved_path: Optional[str] = None

    if os.path.isdir(normalized_path):
        resolved_path = normalized_path
    else:
        resolved_path = ensure_candidate(
            normalized_path, os.path.dirname(normalized_path)
        )

    if resolved_path is None:
        normalized_original = normalized_path.replace("\\", "/")
        resolved_path = _resolve_override_target(
            normalized_original,
            config.get("path_overrides", []),
            ensure_candidate,
        )

    if resolved_path is None:
        folder_name = os.path.basename(normalized_path.rstrip(os.sep))
        if folder_name:
            resolved_path = _resolve_library_target(
                folder_name,
                config.get("file_paths", []),
                ensure_candidate,
            )

    return resolved_path, created


@app.route("/setup", methods=["GET", "POST"])
def setup():
    """Render and process the application setup form."""
    # pylint: disable=too-many-locals,too-many-branches,too-many-return-statements
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
