"""Flask application that bridges YouTube downloads into Radarr."""

# pylint: disable=too-many-lines

import json
import os
import re
import shutil
import stat
import subprocess
import threading
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from glob import glob as glob_paths

import requests  # pylint: disable=import-error
from flask import Flask, jsonify, redirect, render_template, request, url_for  # pylint: disable=import-error

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


def _extract_first_json_block(text: str) -> Optional[str]:
    """Return the first JSON object or array found in the provided text."""

    if not text:
        return None

    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\{\[]", text):
        start = match.start()
        try:
            _, length = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        end = start + length
        return text[start:end]

    return None


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


def _format_duration_label(value: Optional[float]) -> str:
    """Return a human-readable timestamp label for seconds."""

    try:
        total_seconds = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return ""
    if total_seconds <= 0:
        return ""
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"

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


def _job_status(job_id: str, status: str, progress: Optional[float] = None) -> None:
    """Persist a status update for a job."""
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
    "merging playlist videos",
    "saving playlist extra",
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

    cached_config = _CACHE.get("config")
    if isinstance(cached_config, dict):
        return cached_config

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
    except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - configuration file errors
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


def parse_path_overrides(raw_overrides: str) -> Tuple[List[Dict[str, str]], List[str]]:
    """Parse override definitions of the form 'remote => local'."""

    overrides: List[Dict[str, str]] = []
    errors: List[str] = []
    for line_number, line in enumerate(raw_overrides.splitlines(), start=1):
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
                f"Path override line {line_number} must use 'remote => local' format: {cleaned!r}"
            )
            continue
        remote_raw, local_raw = cleaned.split(separator, 1)
        remote = remote_raw.strip()
        local = local_raw.strip()
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
        response = requests.get(
            f"{config['radarr_url']}/api/v3/movie",
            headers={"X-Api-Key": config["radarr_api_key"]},
            timeout=10,
        )
        response.raise_for_status()
        movies = response.json()
        movies.sort(key=lambda movie: movie.get("title", "").lower())
        _CACHE["movies"] = movies
        return movies
    except (requests.RequestException, ValueError) as exc:  # pragma: no cover - network errors
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
    if not movie_label:
        movie_label = "Selected Movie"
    extra = bool(payload.get("extra"))
    extra_type = (payload.get("extraType") or "trailer").strip().lower()
    extra_name = (payload.get("extra_name") or "").strip()
    merge_playlist = bool(payload.get("merge_playlist"))
    playlist_mode = (
        payload.get("playlist_mode")
        or ("merge" if merge_playlist else "single")
    ).strip().lower()
    playlist_extra_entries = [
        entry
        for entry in (payload.get("playlist_extra_entries") or [])
        if isinstance(entry, dict)
    ]
    playlist_extra_types = [
        entry.get("type")
        for entry in playlist_extra_entries
        if normalize_extra_type_key(entry.get("type"))
    ]
    if not playlist_extra_types:
        playlist_extra_types = [
            value
            for value in (
                normalize_extra_type_key(entry)
                for entry in payload.get("playlist_extra_types") or []
            )
            if value
        ]
    if playlist_extra_types:
        extra = True
    extra_label = extra_name or EXTRA_TYPE_LABELS.get(extra_type, extra_type.capitalize())
    if playlist_extra_types:
        extra_label = "Playlist Extras"
    if extra and extra_label:
        label = f"{movie_label} – {extra_label}"
        subtitle = f"Extra • {extra_label}"
    else:
        label = movie_label
        subtitle = ""
    metadata = []
    if extra:
        metadata.append("Stored as extra content")
    if merge_playlist or playlist_mode == "merge":
        metadata.append("Playlist merged into single file")
    if playlist_extra_types:
        readable_types = ", ".join(
            EXTRA_TYPE_LABELS.get(value, value.capitalize()) for value in playlist_extra_types
        )
        if readable_types:
            metadata.append(f"Playlist extras: {readable_types}")
        entry_count = len(playlist_extra_entries) or len(playlist_extra_types)
        if entry_count:
            metadata.append(f"Playlist entries: {entry_count}")
    return {"label": label or "Radarr Download", "subtitle": subtitle, "metadata": metadata}


ALLOWED_PLAYLIST_MODES = {"single", "merge", "extras"}


def _collect_playlist_extra_types(
    raw_values: Any, error: Callable[[str], None]
) -> List[str]:
    """Normalise playlist extra types while recording validation errors."""
    if not isinstance(raw_values, list):
        return []

    collected: List[str] = []
    for entry in raw_values:
        normalized = normalize_extra_type_key(entry)
        if normalized:
            collected.append(normalized)
        elif str(entry).strip():
            error(f"Unknown playlist extra type '{entry}'.")
    return collected


def _collect_playlist_extra_entries(
    raw_entries: Any, error: Callable[[str], None]
) -> List[Dict[str, Any]]:
    """Return normalised playlist extra descriptors from the request payload."""

    if not isinstance(raw_entries, list):
        return []

    collected: List[Dict[str, Any]] = []
    next_index = 1
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            continue
        raw_index = raw_entry.get("index")
        try:
            index_value = int(raw_index)
        except (TypeError, ValueError):
            index_value = None
        if index_value is None or index_value < 1:
            index_value = next_index
        next_index = index_value + 1

        raw_type = raw_entry.get("type")
        normalized_type = normalize_extra_type_key(raw_type)
        if not normalized_type:
            if str(raw_type or "").strip():
                error(f"Unknown extra type '{raw_type}'.")
            normalized_type = "other"

        entry: Dict[str, Any] = {
            "index": index_value,
            "type": normalized_type,
            "name": str(raw_entry.get("name") or "").strip(),
            "title": str(raw_entry.get("title") or "").strip(),
            "id": str(raw_entry.get("id") or "").strip(),
        }

        raw_duration = raw_entry.get("duration")
        try:
            entry["duration"] = int(raw_duration)
        except (TypeError, ValueError):
            entry["duration"] = None

        collected.append(entry)

    collected.sort(key=lambda item: item.get("index") or 0)
    for normalised_index, entry in enumerate(collected, start=1):
        entry["index"] = normalised_index
    return collected


def _fetch_playlist_preview(
    yt_url: str, cookie_path: Optional[str], limit: int
) -> Dict[str, Any]:
    """Query yt-dlp for playlist entries without downloading videos."""

    limit = max(1, min(limit, 200))
    command = [
        "yt-dlp",
        "--ignore-config",
        "--skip-download",
        "--dump-single-json",
        "--no-warnings",
        "--no-progress",
        "--playlist-end",
        str(limit),
    ]
    if cookie_path:
        command += ["--cookies", cookie_path]
    command.append(yt_url)

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - runtime dependency missing
        raise FileNotFoundError("yt-dlp executable not found.") from exc
    except OSError as exc:  # pragma: no cover - subprocess failure
        raise RuntimeError(f"Failed to invoke yt-dlp: {exc}") from exc

    if result.returncode not in (0, None):
        stderr = (result.stderr or "").strip() or "Unknown error"
        raise RuntimeError(f"yt-dlp reported an error: {stderr}")

    payload: Dict[str, Any]
    stdout_text = result.stdout or ""
    try:
        payload = json.loads(stdout_text or "{}")
    except json.JSONDecodeError:
        json_block = _extract_first_json_block(stdout_text)
        if not json_block:
            raise RuntimeError("Failed to parse yt-dlp response.")
        try:
            payload = json.loads(json_block)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Failed to parse yt-dlp response.") from exc

    entries: List[Dict[str, Any]] = []
    playlist_title = ""
    total_count = 0
    truncated = False

    if isinstance(payload, dict):
        payload_type = payload.get("_type")
        if payload_type == "playlist" and isinstance(payload.get("entries"), list):
            raw_entries = payload.get("entries") or []
            playlist_title = str(payload.get("title") or "").strip()
            try:
                total_count = int(payload.get("playlist_count") or len(raw_entries))
            except (TypeError, ValueError):
                total_count = len(raw_entries)
        else:
            raw_entries = [payload]
            playlist_title = str(payload.get("title") or "").strip()
            total_count = len(raw_entries)
    else:
        raw_entries = []

    for idx, raw_entry in enumerate(raw_entries, start=1):
        if not isinstance(raw_entry, dict):
            continue
        raw_index = raw_entry.get("playlist_index") or idx
        try:
            playlist_index = int(raw_index)
        except (TypeError, ValueError):
            playlist_index = idx

        raw_duration = raw_entry.get("duration")
        try:
            duration_seconds = int(raw_duration)
        except (TypeError, ValueError):
            duration_seconds = None

        duration_string = raw_entry.get("duration_string")
        if isinstance(duration_string, str):
            duration_label = duration_string.strip()
        elif duration_string is None:
            duration_label = ""
        else:
            duration_label = str(duration_string).strip()
        if not duration_label:
            duration_label = _format_duration_label(duration_seconds)

        raw_title_value = raw_entry.get("title")
        if isinstance(raw_title_value, str):
            entry_title = raw_title_value.strip()
        elif raw_title_value is None:
            entry_title = ""
        else:
            entry_title = str(raw_title_value).strip()
        if not entry_title:
            entry_title = f"Entry {playlist_index}"

        entries.append(
            {
                "index": playlist_index,
                "title": entry_title,
                "id": str(raw_entry.get("id") or ""),
                "duration": duration_seconds,
                "duration_text": duration_label,
            }
        )

    entries.sort(key=lambda item: item.get("index") or 0)
    for normalised_index, entry in enumerate(entries, start=1):
        entry["index"] = normalised_index

    if total_count and len(entries) < total_count:
        truncated = True

    return {
        "entries": entries,
        "playlist_title": playlist_title,
        "total_count": total_count or len(entries),
        "truncated": truncated,
    }


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


def _resolve_playlist_request(
    data: Dict, error: Callable[[str], None]
) -> Tuple[str, List[Dict[str, Any]], List[str]]:
    """Return playlist mode, entries, and extra types from the payload."""

    playlist_mode = (data.get("playlist_mode") or "single").strip().lower()
    if playlist_mode not in ALLOWED_PLAYLIST_MODES:
        error("Invalid playlist handling option selected.")
        playlist_mode = "single"

    playlist_extra_entries = _collect_playlist_extra_entries(
        data.get("playlist_extra_entries"), error
    )
    playlist_extra_types = [
        entry.get("type") for entry in playlist_extra_entries if entry.get("type")
    ]
    if not playlist_extra_types:
        playlist_extra_types = _collect_playlist_extra_types(
            data.get("playlist_extra_types"), error
        )

    return playlist_mode, playlist_extra_entries, playlist_extra_types


def _resolve_extra_settings(
    data: Dict,
    playlist_mode: str,
    playlist_extra_entries: List[Dict[str, Any]],
    playlist_extra_types: List[str],
    error: Callable[[str], None],
) -> Tuple[bool, str, str]:
    """Determine the extra storage options for the request."""

    extra_requested = bool(data.get("extra")) or bool(playlist_extra_types)
    extra_name = (data.get("extra_name") or "").strip()

    if playlist_mode == "extras":
        if not extra_requested:
            error("Playlist extras require storing the videos as extras.")
        if not playlist_extra_types and not playlist_extra_entries:
            error("Provide at least one extra type for the playlist entries.")
        extra_requested = True
    elif extra_requested and not extra_name:
        error("Extra name is required when storing in a subfolder.")

    selected_extra_type = (data.get("extraType") or "trailer").strip().lower()
    if playlist_mode == "extras" and playlist_extra_types:
        selected_extra_type = playlist_extra_types[0]

    return extra_requested, extra_name, selected_extra_type


def _prepare_create_payload(data: Dict, error: Callable[[str], None]) -> Dict:
    """Validate and sanitise the incoming create payload."""

    playlist_mode, playlist_extra_entries, playlist_extra_types = _resolve_playlist_request(
        data, error
    )

    extra_requested, extra_name, selected_extra_type = _resolve_extra_settings(
        data, playlist_mode, playlist_extra_entries, playlist_extra_types, error
    )

    return {
        "yturl": _validate_request_urls(data, error),
        "movieId": _validate_movie_selection(data, error),
        "movieName": (data.get("movieName") or "").strip(),
        "title": (data.get("title") or "").strip(),
        "year": (data.get("year") or "").strip(),
        "tmdb": (data.get("tmdb") or "").strip(),
        "extra": extra_requested,
        "extraType": selected_extra_type,
        "extra_name": extra_name,
        "merge_playlist": playlist_mode == "merge",
        "playlist_mode": playlist_mode,
        "playlist_extra_types": playlist_extra_types,
        "playlist_extra_entries": playlist_extra_entries,
    }


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


@app.route("/playlist_preview", methods=["POST"])
def playlist_preview():
    """Return playlist details for the provided YouTube URL."""

    config = load_config()
    data = request.get_json(silent=True) or {}
    yt_url = (data.get("yturl") or data.get("url") or "").strip()
    if not yt_url:
        return jsonify({"error": "YouTube URL is required."}), 400
    if not re.search(r"(youtube\.com|youtu\.be)/", yt_url, re.IGNORECASE):
        return jsonify({"error": "Please provide a valid YouTube URL."}), 400

    limit_raw = data.get("limit")
    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))

    cookie_path = get_cookie_path(config)
    try:
        preview = _fetch_playlist_preview(yt_url, cookie_path, limit)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:  # pragma: no cover - unexpected failure
        return jsonify({"error": f"Unexpected playlist probe error: {exc}"}), 500

    preview["limit"] = limit
    preview["debug_mode"] = config.get("debug_mode", False)
    return jsonify(preview)


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

    worker = threading.Thread(target=process_download_job, args=(job_id, payload), daemon=True)
    worker.start()

    display_job = dict(job_record)
    display_job["logs"] = _filter_logs_for_display(
        display_job.get("logs", []), config.get("debug_mode", False)
    )

    return jsonify({"job": display_job, "debug_mode": config.get("debug_mode", False)}), 202


def process_download_job(job_id: str, payload: Dict) -> None:
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
        merge_playlist = bool(payload.get("merge_playlist"))
        playlist_mode = (
            payload.get("playlist_mode")
            or ("merge" if merge_playlist else "single")
        ).strip().lower()
        raw_playlist_extra_entries = payload.get("playlist_extra_entries") or []
        playlist_extra_entries: List[Dict[str, Any]] = []
        for raw_entry in raw_playlist_extra_entries:
            if not isinstance(raw_entry, dict):
                continue
            raw_index = raw_entry.get("index")
            try:
                index_value = int(raw_index)
            except (TypeError, ValueError):
                index_value = len(playlist_extra_entries) + 1

            normalized_type = normalize_extra_type_key(raw_entry.get("type")) or "other"
            normalised_entry: Dict[str, Any] = {
                "index": index_value,
                "type": normalized_type,
                "name": str(raw_entry.get("name") or "").strip(),
                "title": str(raw_entry.get("title") or "").strip(),
            }

            raw_duration = raw_entry.get("duration")
            if isinstance(raw_duration, (int, float)):
                normalised_entry["duration"] = int(raw_duration)
            elif raw_duration is not None:
                normalised_entry["duration"] = None

            identifier = str(raw_entry.get("id") or "").strip()
            if identifier:
                normalised_entry["id"] = identifier

            playlist_extra_entries.append(normalised_entry)

        playlist_extra_entries.sort(key=lambda item: item.get("index") or 0)
        for normalised_index, entry in enumerate(playlist_extra_entries, start=1):
            entry["index"] = normalised_index

        playlist_extra_types: List[str] = [
            entry.get("type")
            for entry in playlist_extra_entries
            if entry.get("type")
        ]
        if not playlist_extra_types:
            raw_playlist_extra_types = payload.get("playlist_extra_types") or []
            for entry in raw_playlist_extra_types:
                normalized = normalize_extra_type_key(entry)
                if normalized:
                    playlist_extra_types.append(normalized)
        if playlist_mode == "extras" and not playlist_extra_types:
            warn("Playlist extras mode selected but no valid extra types were provided.")
        if playlist_mode != "extras":
            playlist_extra_types = []
            playlist_extra_entries = []
        merge_playlist = playlist_mode == "merge"
        payload["playlist_mode"] = playlist_mode
        payload["playlist_extra_types"] = playlist_extra_types
        payload["playlist_extra_entries"] = playlist_extra_entries
        payload["merge_playlist"] = merge_playlist

        resolved = resolve_movie_by_metadata(movie_id, tmdb, title, year, log)
        if resolved is None or not str(resolved.get("id")):
            fail("No movie selected. Please choose a movie from the suggestions list.")
            return
        movie_id = str(resolved.get("id"))

        extra_type = (payload.get("extraType") or "trailer").strip().lower()
        if playlist_extra_types:
            extra_type = playlist_extra_types[0]
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

        extra = bool(payload.get("extra")) or bool(playlist_extra_types) or bool(
            playlist_extra_entries
        )
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
        except (requests.RequestException, ValueError) as exc:  # pragma: no cover - network errors
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
        if playlist_extra_types:
            log(
                "Playlist extras requested; videos will be saved into their "
                "respective extra subfolders."
            )
        elif extra:
            subfolder = folder_map.get(extra_type, extra_type.capitalize() + "s")
            target_dir = os.path.join(movie_path, subfolder)
            os.makedirs(target_dir, exist_ok=True)
            log(f"Storing video in subfolder '{subfolder}'.")
        else:
            log("Treating video as main video file.")

        if merge_playlist:
            log("Playlist download requested; videos will be merged into a single file.")
        elif playlist_extra_types:
            readable_types = ", ".join(
                EXTRA_TYPE_LABELS.get(value, value.capitalize())
                for value in playlist_extra_types
            )
            if readable_types:
                log(
                    "Playlist download requested; entries will be processed as "
                    f"extras ({readable_types})."
                )
            else:
                log(
                    "Playlist download requested; entries will be processed as extras."
                )

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
        default_label = "Playlist" if (merge_playlist or playlist_extra_types) else "Video"
        if descriptive:
            log(f"Using custom descriptive name '{descriptive}'.")
        elif merge_playlist or playlist_extra_types:
            try:
                log("Querying yt-dlp for playlist title.")
                yt_cmd = [
                    "yt-dlp",
                    "--skip-download",
                    "--print",
                    "%(playlist_title)s",
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
                titles = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
                playlist_title = titles[0] if titles else ""
                descriptive = playlist_title or default_label
                if playlist_title:
                    log(f"Using playlist title '{playlist_title}'.")
                else:
                    warn(
                        f"Playlist title was empty. Using fallback name '{default_label}'."
                    )
            except (
                subprocess.CalledProcessError,
                FileNotFoundError,
                OSError,
            ) as exc:  # pragma: no cover - command failure
                descriptive = default_label
                warn(
                    "Failed to retrieve playlist title from yt-dlp "
                    f"({exc}). Using fallback name '{default_label}'."
                )
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
                descriptive = proc.stdout.strip() or default_label
                log(f"Using YouTube title '{descriptive}'.")
            except (
                subprocess.CalledProcessError,
                FileNotFoundError,
                OSError,
            ) as exc:  # pragma: no cover - command failure
                descriptive = default_label
                warn(
                    "Failed to retrieve title from yt-dlp "
                    f"({exc}). Using fallback name '{default_label}'."
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
        pattern = os.path.join(target_dir, f"{filename_base}.*")
        if any(os.path.exists(path) for path in glob_paths(pattern)):
            log(f"File stem '{filename_base}' already exists. Searching for a free filename.")
            suffix_index = 1
            while True:
                candidate_base = f"{filename_base} ({suffix_index})"
                candidate_pattern = os.path.join(target_dir, f"{candidate_base}.*")
                if not any(os.path.exists(path) for path in glob_paths(candidate_pattern)):
                    filename_base = candidate_base
                    log(f"Selected new filename stem '{filename_base}'.")
                    break
                suffix_index += 1

        template_base = filename_base.replace("%", "%%")
        playlist_temp_dir: Optional[str] = None
        if merge_playlist or playlist_extra_types:
            playlist_temp_dir = os.path.join(target_dir, f".yt2radarr_playlist_{job_id}")
            os.makedirs(playlist_temp_dir, exist_ok=True)
            if merge_playlist:
                log(
                    "Playlist merge enabled. Downloads will be staged in "
                    f"'{os.path.basename(playlist_temp_dir)}'."
                )
            else:
                log(
                    "Playlist extras enabled. Downloads will be staged in "
                    f"'{os.path.basename(playlist_temp_dir)}'."
                )
            target_template = os.path.join(
                playlist_temp_dir, "%(playlist_index)05d - %(title)s.%(ext)s"
            )
            expected_pattern = os.path.join(playlist_temp_dir, "*.*")
        else:
            target_template = os.path.join(target_dir, f"{template_base}.%(ext)s")
            expected_pattern = os.path.join(target_dir, f"{filename_base}.*")

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
        if merge_playlist or playlist_extra_types:
            info_command.append("--yes-playlist")
        info_command += [
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
        except (
            subprocess.CalledProcessError,
            FileNotFoundError,
            OSError,
        ) as exc:  # pragma: no cover - command failure
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
        command += ["-f", format_selector]
        if merge_playlist or playlist_extra_types:
            command.append("--yes-playlist")
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

        try:
            with subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=target_dir,
            ) as process:
                assert process.stdout is not None
                for raw_line in process.stdout:
                    line = raw_line.rstrip()
                    if not line:
                        continue
                    handle_output_line(line)
                return_code = process.wait()
        except (OSError, ValueError) as exc:  # pragma: no cover - command failure
            fail(f"Failed to invoke yt-dlp: {exc}")
            return

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

            try:
                merge_result = subprocess.run(
                    merge_command,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except (OSError, ValueError) as exc:
                fail(f"Failed to invoke ffmpeg for playlist merge: {exc}")
                return

            if merge_result.stdout:
                for line in merge_result.stdout.strip().splitlines():
                    debug(f"ffmpeg: {line}")
            if merge_result.stderr:
                for line in merge_result.stderr.strip().splitlines():
                    debug(f"ffmpeg: {line}")

            if merge_result.returncode != 0 or not os.path.exists(merged_output_path):
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

        if playlist_extra_types:
            if not playlist_temp_dir:
                fail("Internal error: playlist staging directory was not created.")
                return

            active_candidates = [
                path
                for path in downloaded_candidates
                if os.path.isfile(path) and not _is_intermediate_file(path)
            ]
            if not active_candidates:
                active_candidates = [
                    path for path in downloaded_candidates if os.path.isfile(path)
                ]
            if not active_candidates:
                fail(
                    "Playlist extras download completed but no video files were produced."
                )
                return

            active_candidates.sort()

            fallback_type = extra_type
            if playlist_extra_entries:
                last_type = playlist_extra_entries[-1].get("type")
                if last_type:
                    fallback_type = last_type
            elif playlist_extra_types:
                fallback_type = playlist_extra_types[-1]
            if fallback_type not in allowed_extra_types:
                fallback_type = "other"

            provided_count = len(playlist_extra_entries) or len(playlist_extra_types)
            if provided_count and len(active_candidates) > provided_count:
                fallback_label = EXTRA_TYPE_LABELS.get(
                    fallback_type, fallback_type.capitalize()
                )
                warn(
                    "Playlist contained "
                    f"{len(active_candidates)} entries but only {provided_count} "
                    "extra descriptors were supplied. Remaining entries will use "
                    f"'{fallback_label}'."
                )

            saved_paths: List[str] = []

            for entry_index, candidate in enumerate(active_candidates, start=1):
                entry_details: Optional[Dict[str, Any]] = None
                if entry_index - 1 < len(playlist_extra_entries):
                    entry_details = playlist_extra_entries[entry_index - 1]

                assigned_type = None
                if entry_details and entry_details.get("type") in allowed_extra_types:
                    assigned_type = entry_details.get("type")
                elif entry_index - 1 < len(playlist_extra_types):
                    assigned_type = playlist_extra_types[entry_index - 1]
                if not assigned_type:
                    assigned_type = fallback_type
                if assigned_type not in allowed_extra_types:
                    assigned_type = "other"

                label = EXTRA_TYPE_LABELS.get(assigned_type, assigned_type.capitalize())
                dest_subfolder = folder_map.get(
                    assigned_type, assigned_type.capitalize() + "s"
                )
                dest_dir = os.path.join(movie_path, dest_subfolder)
                os.makedirs(dest_dir, exist_ok=True)

                entry_title = ""
                if entry_details and entry_details.get("title"):
                    entry_title = str(entry_details.get("title")).strip()
                if not entry_title:
                    entry_title = os.path.splitext(os.path.basename(candidate))[0]
                sanitized_entry_title = sanitize_filename(entry_title)
                base_label = sanitize_filename(label) or assigned_type
                custom_name = ""
                if entry_details and entry_details.get("name"):
                    custom_name = str(entry_details.get("name")).strip()
                sanitized_custom = sanitize_filename(custom_name)

                canonical_parts = [part for part in [movie_stem, base_label] if part]
                if sanitized_custom:
                    canonical_parts.append(sanitized_custom)
                elif sanitized_entry_title:
                    lowered_stem = " ".join(canonical_parts).lower()
                    if sanitized_entry_title.lower() not in lowered_stem:
                        canonical_parts.append(sanitized_entry_title)
                canonical_stem = " ".join(canonical_parts).strip()
                canonical_stem = canonical_stem or f"{movie_stem} {base_label}".strip()
                canonical_stem = sanitize_filename(canonical_stem) or (
                    f"{movie_stem} {base_label}".strip() or movie_stem
                )

                extension = os.path.splitext(candidate)[1] or ""
                dest_filename = f"{canonical_stem}{extension}"
                if os.path.exists(os.path.join(dest_dir, dest_filename)):
                    suffix_index = 1
                    base_name, ext_part = os.path.splitext(dest_filename)
                    while True:
                        suffix_index += 1
                        alt_filename = f"{base_name} ({suffix_index}){ext_part}"
                        if not os.path.exists(os.path.join(dest_dir, alt_filename)):
                            dest_filename = alt_filename
                            break
                dest_path = os.path.join(dest_dir, dest_filename)

                try:
                    os.replace(candidate, dest_path)
                except OSError as exc:
                    fail(
                        "Failed to move playlist extra '{label}' to "
                        f"'{dest_filename}': {exc}"
                    )
                    return

                saved_paths.append(dest_path)
                display_label = label
                if custom_name:
                    display_label = f"{label} – {custom_name}".strip()
                elif entry_title and entry_title.lower() not in label.lower():
                    display_label = f"{label} – {entry_title}".strip()
                log(
                    f"Saving playlist extra #{entry_index}: '{display_label}' -> "
                    f"'{dest_filename}'."
                )

            if playlist_temp_dir and os.path.isdir(playlist_temp_dir):
                try:
                    shutil.rmtree(playlist_temp_dir)
                except OSError:
                    pass

            _job_status(job_id, "processing", progress=100)
            log(f"Success! Saved {len(saved_paths)} playlist extras.")
            _mark_job_success(job_id)
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
    # pylint: disable=broad-exception-caught
    except Exception as exc:  # pragma: no cover - unexpected failure
        fail(f"Unexpected error: {exc}")
    # pylint: enable=broad-exception-caught


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
