document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('movieForm');
  if (!form) {
    return;
  }

  const ytInput = document.getElementById('yturl');
  const movieNameInput = document.getElementById('movieName');
  const movieOptions = document.getElementById('movieOptions');
  const movieIdInput = document.getElementById('movieId');
  const titleInput = document.getElementById('title');
  const yearInput = document.getElementById('year');
  const tmdbInput = document.getElementById('tmdb');
  const resSelect = document.getElementById('resolution');
  const extSelect = document.getElementById('extension');
  const extraCheckbox = document.getElementById('extra');
  const extraTypeSelect = document.getElementById('extraType');
  const extraFields = document.getElementById('extraFields');
  const extraNameInput = document.getElementById('extra_name');
  const consoleDiv = document.getElementById('console');
  const downloadsList = document.getElementById('downloadsList');

  let downloadEntries = [];
  const progressTimers = new Map();
  const STATUS_LABELS = {
    queued: 'Queued',
    processing: 'Processing',
    complete: 'Completed',
    failed: 'Failed'
  };
  const RESOLUTION_LABELS = {
    best: 'Best Available',
    '1080p': 'Up to 1080p',
    '720p': 'Up to 720p',
    '480p': 'Up to 480p'
  };
  const EXTRA_TYPE_LABELS = {
    trailer: 'Trailer',
    behindthescenes: 'Behind the Scenes',
    deleted: 'Deleted Scene',
    featurette: 'Featurette',
    interview: 'Interview',
    scene: 'Scene',
    short: 'Short',
    other: 'Other'
  };
  const MAX_DOWNLOAD_ENTRIES = 8;

  function appendConsoleLine(text, isError) {
    if (!consoleDiv) {
      return;
    }
    const lineElem = document.createElement('div');
    if (isError) {
      lineElem.classList.add('error-line');
    }
    lineElem.textContent = text;
    consoleDiv.appendChild(lineElem);
    consoleDiv.scrollTop = consoleDiv.scrollHeight;
  }

  function resetConsole(message) {
    if (!consoleDiv) {
      return;
    }
    consoleDiv.innerHTML = '';
    if (message) {
      appendConsoleLine(message, false);
    }
  }

  function normalizeExtraLabel(type) {
    if (!type) {
      return '';
    }
    return EXTRA_TYPE_LABELS[type] || type;
  }

  function formatTime(value) {
    if (!value) {
      return '';
    }
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) {
      return '';
    }
    try {
      return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch (err) {
      return date.toLocaleTimeString();
    }
  }

  function cleanErrorText(text) {
    if (!text) {
      return '';
    }
    return text.replace(/^ERROR:\s*/i, '').trim();
  }

  function buildDownloadItem(entry) {
    const wrapper = document.createElement('div');
    wrapper.className = 'download-item';
    wrapper.dataset.status = entry.status;

    const header = document.createElement('div');
    header.className = 'item-header';

    const title = document.createElement('div');
    title.className = 'item-title';
    title.textContent = entry.label;
    header.appendChild(title);

    const meta = document.createElement('div');
    meta.className = 'item-meta';

    const statusPill = document.createElement('span');
    statusPill.className = 'status-pill';
    statusPill.dataset.status = entry.status;
    statusPill.textContent = STATUS_LABELS[entry.status] || entry.status;
    meta.appendChild(statusPill);

    if (entry.subtitle) {
      const subtitle = document.createElement('span');
      subtitle.textContent = entry.subtitle;
      meta.appendChild(subtitle);
    }

    header.appendChild(meta);
    wrapper.appendChild(header);

    const progressBar = document.createElement('div');
    progressBar.className = 'progress-bar';
    const progressFill = document.createElement('div');
    progressFill.className = 'progress-fill';
    const progressValue = Math.max(0, Math.min(100, entry.progress || 0));
    progressFill.style.width = `${progressValue}%`;
    progressBar.appendChild(progressFill);
    wrapper.appendChild(progressBar);

    if (entry.message && entry.status === 'failed') {
      const message = document.createElement('div');
      message.className = 'download-error';
      message.textContent = entry.message;
      wrapper.appendChild(message);
    }

    const footer = document.createElement('div');
    footer.className = 'item-footer';
    const footerLeft = document.createElement('span');
    const metadataText = (entry.metadata || []).filter(Boolean).join(' • ');
    footerLeft.textContent = metadataText || ' ';
    footer.appendChild(footerLeft);
    const footerRight = document.createElement('span');
    const timestamp = entry.status === 'complete' || entry.status === 'failed'
      ? formatTime(entry.updatedAt || entry.startedAt)
      : formatTime(entry.startedAt);
    footerRight.textContent = timestamp;
    footer.appendChild(footerRight);
    wrapper.appendChild(footer);

    return wrapper;
  }

  function renderDownloads() {
    if (!downloadsList) {
      return;
    }
    downloadsList.innerHTML = '';
    if (!downloadEntries.length) {
      downloadsList.classList.add('empty');
      const empty = document.createElement('div');
      empty.className = 'empty-state';
      empty.textContent = 'No downloads yet.';
      downloadsList.appendChild(empty);
      return;
    }
    downloadsList.classList.remove('empty');
    downloadEntries.forEach(entry => {
      downloadsList.appendChild(buildDownloadItem(entry));
    });
  }

  function stopProgress(id) {
    const timer = progressTimers.get(id);
    if (timer) {
      clearInterval(timer);
      progressTimers.delete(id);
    }
  }

  function upsertDownload(update) {
    if (!downloadsList || !update || !update.id) {
      return;
    }
    const index = downloadEntries.findIndex(item => item.id === update.id);
    if (index >= 0) {
      const existing = downloadEntries[index];
      const nextEntry = {
        ...existing,
        ...update,
        metadata: update.metadata || existing.metadata,
        subtitle: update.subtitle !== undefined ? update.subtitle : existing.subtitle,
        message: update.message !== undefined ? update.message : existing.message,
        startedAt: existing.startedAt,
        updatedAt: new Date()
      };
      downloadEntries[index] = nextEntry;
    } else {
      const entry = {
        metadata: [],
        subtitle: '',
        message: '',
        progress: 0,
        ...update,
        progress: update.progress !== undefined ? update.progress : 0,
        startedAt: update.startedAt || new Date(),
        updatedAt: new Date()
      };
      downloadEntries.unshift(entry);
      if (downloadEntries.length > MAX_DOWNLOAD_ENTRIES) {
        const removed = downloadEntries.splice(MAX_DOWNLOAD_ENTRIES);
        removed.forEach(item => stopProgress(item.id));
      }
    }
    renderDownloads();
  }

  function startProgress(id) {
    if (!downloadsList || progressTimers.has(id)) {
      return;
    }
    const timer = setInterval(() => {
      const entry = downloadEntries.find(item => item.id === id);
      if (!entry) {
        clearInterval(timer);
        progressTimers.delete(id);
        return;
      }
      if (entry.status !== 'processing') {
        clearInterval(timer);
        progressTimers.delete(id);
        return;
      }
      const nextProgress = Math.min(90, (entry.progress || 0) + (Math.random() * 10 + 5));
      upsertDownload({ id, progress: nextProgress });
    }, 1500);
    progressTimers.set(id, timer);
  }

  function createDownloadEntry(payload) {
    if (!downloadsList) {
      return null;
    }
    const now = new Date();
    const fallbackTitle = payload.title || titleInput.value.trim();
    const movieLabel = payload.movieName || fallbackTitle || 'Selected Movie';
    const extraDescriptor = payload.extra
      ? (payload.extra_name || normalizeExtraLabel(payload.extraType))
      : '';
    const label = extraDescriptor ? `${movieLabel} – ${extraDescriptor}` : movieLabel;
    const metadata = [
      `Format: ${(payload.extension || 'mp4').toUpperCase()}`,
      `Resolution: ${RESOLUTION_LABELS[payload.resolution] || payload.resolution || 'Best Available'}`
    ];
    if (payload.extra) {
      metadata.unshift(`Stored as extra content`);
    }
    return {
      id: `dl-${now.getTime()}-${Math.floor(Math.random() * 1000)}`,
      label: label || 'Radarr Download',
      status: 'processing',
      progress: 18,
      metadata,
      subtitle: payload.extra ? `Extra • ${extraDescriptor || normalizeExtraLabel(payload.extraType)}` : '',
      startedAt: now,
      updatedAt: now,
      message: ''
    };
  }

  function completeDownload(entryId, status, message) {
    if (!entryId) {
      return;
    }
    stopProgress(entryId);
    upsertDownload({
      id: entryId,
      status,
      progress: 100,
      message: status === 'failed' ? message : ''
    });
  }

  function updateExtraVisibility() {
    if (extraCheckbox.checked) {
      extraFields.style.display = 'block';
      extraNameInput.required = true;
    } else {
      extraFields.style.display = 'none';
      extraNameInput.required = false;
      extraNameInput.value = '';
      extraTypeSelect.value = 'trailer';
    }
  }

  extraCheckbox.addEventListener('change', updateExtraVisibility);

  movieNameInput.addEventListener('input', () => {
    const inputVal = movieNameInput.value;
    let matched = false;
    for (let i = 0; i < movieOptions.options.length; i += 1) {
      const option = movieOptions.options[i];
      if (option.value === inputVal) {
        matched = true;
        movieIdInput.value = option.getAttribute('data-id') || '';
        titleInput.value = option.getAttribute('data-title') || '';
        yearInput.value = option.getAttribute('data-year') || '';
        tmdbInput.value = option.getAttribute('data-tmdb') || '';
        break;
      }
    }
    if (!matched) {
      movieIdInput.value = '';
      titleInput.value = '';
      yearInput.value = '';
      tmdbInput.value = '';
    }
  });

  form.addEventListener('submit', event => {
    event.preventDefault();

    const payload = {
      yturl: ytInput.value.trim(),
      movieName: movieNameInput.value.trim(),
      movieId: movieIdInput.value.trim(),
      title: titleInput.value.trim(),
      year: yearInput.value.trim(),
      tmdb: tmdbInput.value.trim(),
      resolution: resSelect.value,
      extension: extSelect.value,
      extra: extraCheckbox.checked,
      extraType: extraTypeSelect.value,
      extra_name: extraNameInput.value.trim()
    };

    resetConsole('Submitting request...');

    if (!payload.yturl) {
      appendConsoleLine('ERROR: YouTube URL is required.', true);
      return;
    }
    const ytPattern = /^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.be)\//;
    if (!ytPattern.test(payload.yturl)) {
      appendConsoleLine('ERROR: Please enter a valid YouTube URL.', true);
      return;
    }
    if (!payload.movieId) {
      appendConsoleLine('ERROR: Please select a valid movie from the list.', true);
      return;
    }
    if (payload.extra && !payload.extra_name) {
      appendConsoleLine('ERROR: Please provide an extra name.', true);
      return;
    }

    const downloadEntry = createDownloadEntry(payload);
    const entryId = downloadEntry ? downloadEntry.id : null;
    if (downloadEntry) {
      upsertDownload(downloadEntry);
      startProgress(downloadEntry.id);
    }

    fetch('/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
      .then(response => response.json().then(data => ({ ok: response.ok, data })))
      .then(({ ok, data }) => {
        if (consoleDiv) {
          consoleDiv.innerHTML = '';
        }
        const logs = Array.isArray(data.logs) ? data.logs : [];
        if (logs.length > 0) {
          logs.forEach(line => {
            const isError = typeof line === 'string' && line.startsWith('ERROR');
            appendConsoleLine(line, isError);
          });
        } else {
          appendConsoleLine('No output.', false);
        }
        const errorLines = logs.filter(line => typeof line === 'string' && line.startsWith('ERROR'));
        const hasError = !ok || errorLines.length > 0;
        if (!ok && errorLines.length === 0) {
          appendConsoleLine('ERROR: Request failed.', true);
        }
        if (entryId) {
          const message = hasError ? cleanErrorText(errorLines[0] || 'Request failed.') : '';
          completeDownload(entryId, hasError ? 'failed' : 'complete', message);
        }
      })
      .catch(err => {
        if (consoleDiv) {
          consoleDiv.innerHTML = '';
        }
        const message = err && err.message ? err.message : String(err);
        appendConsoleLine('ERROR: ' + message, true);
        if (entryId) {
          completeDownload(entryId, 'failed', message);
        }
      });
  });

  updateExtraVisibility();
  renderDownloads();
});
