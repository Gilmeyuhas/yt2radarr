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
  const STATUS_LABELS = {
    queued: 'Queued',
    processing: 'Processing',
    complete: 'Completed',
    failed: 'Failed'
  };
  const MAX_DOWNLOAD_ENTRIES = 8;
  const LIST_REFRESH_INTERVAL = 8000;
  const jobPollers = new Map();
  let listRefreshTimer = null;
  let activeJobId = null;

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

  function parseDate(value) {
    if (!value) {
      return null;
    }
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) {
      return null;
    }
    return date;
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

  function replaceDownloads(entries) {
    downloadEntries = entries.slice(0, MAX_DOWNLOAD_ENTRIES);
    renderDownloads();
  }

  function upsertDownload(update, options = {}) {
    if (!downloadsList || !update || !update.id) {
      return;
    }
    const position = options.position === 'end' ? 'end' : 'start';
    const index = downloadEntries.findIndex(item => item.id === update.id);
    if (index >= 0) {
      const existing = downloadEntries[index];
      const nextEntry = {
        ...existing,
        ...update,
        metadata: update.metadata !== undefined ? update.metadata : existing.metadata,
        subtitle: update.subtitle !== undefined ? update.subtitle : existing.subtitle,
        message: update.message !== undefined ? update.message : existing.message,
        startedAt: existing.startedAt,
        updatedAt: update.updatedAt || new Date()
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
        updatedAt: update.updatedAt || new Date()
      };
      if (position === 'end') {
        downloadEntries.push(entry);
      } else {
        downloadEntries.unshift(entry);
      }
      if (downloadEntries.length > MAX_DOWNLOAD_ENTRIES) {
        downloadEntries.splice(MAX_DOWNLOAD_ENTRIES);
      }
    }
    renderDownloads();
  }

  function showJobLogs(job) {
    if (!consoleDiv) {
      return;
    }
    consoleDiv.innerHTML = '';
    const logs = job && Array.isArray(job.logs) ? job.logs : [];
    if (!logs.length) {
      appendConsoleLine('No output yet.', false);
      return;
    }
    logs.forEach(line => {
      const isError = typeof line === 'string' && line.startsWith('ERROR');
      appendConsoleLine(line, isError);
    });
  }

  function stopJobPolling(jobId) {
    const info = jobPollers.get(jobId);
    if (info && info.timer) {
      clearInterval(info.timer);
    }
    jobPollers.delete(jobId);
  }

  function jobToEntry(job) {
    if (!job || !job.id) {
      return null;
    }
    let status = job.status || 'queued';
    if (status === 'completed') {
      status = 'complete';
    }
    if (!STATUS_LABELS[status]) {
      status = 'queued';
    }
    const startedAt = parseDate(job.started_at || job.created_at) || new Date();
    const updatedAt = parseDate(job.updated_at || job.started_at || job.created_at) || new Date();
    const progress = typeof job.progress === 'number' ? Math.max(0, Math.min(100, job.progress)) : 0;
    return {
      id: job.id,
      label: job.label || 'Radarr Download',
      status,
      progress,
      metadata: Array.isArray(job.metadata) ? job.metadata : [],
      subtitle: job.subtitle || '',
      message: job.message || '',
      startedAt,
      updatedAt
    };
  }

  function pollJob(jobId, showConsole) {
    if (!jobId) {
      return;
    }
    fetch(`/jobs/${encodeURIComponent(jobId)}`)
      .then(response => {
        if (!response.ok) {
          if (response.status === 404) {
            stopJobPolling(jobId);
          }
          return null;
        }
        return response.json();
      })
      .then(data => {
        if (!data || !data.job) {
          return;
        }
        const entry = jobToEntry(data.job);
        if (entry) {
          upsertDownload(entry);
          if (entry.status === 'complete' || entry.status === 'failed') {
            stopJobPolling(jobId);
          }
        }
        if (showConsole || activeJobId === jobId) {
          activeJobId = jobId;
          showJobLogs(data.job);
        }
      })
      .catch(() => {});
  }

  function beginJobPolling(jobId, showConsole = false) {
    if (!jobId) {
      return;
    }
    const existing = jobPollers.get(jobId);
    if (existing) {
      if (showConsole) {
        existing.showConsole = true;
      }
      return;
    }
    const info = {
      showConsole,
      timer: null
    };
    const tick = () => {
      const current = jobPollers.get(jobId);
      const shouldShow = current ? current.showConsole : false;
      pollJob(jobId, shouldShow);
    };
    info.timer = setInterval(tick, 3000);
    jobPollers.set(jobId, info);
    pollJob(jobId, showConsole);
  }

  function refreshJobs() {
    fetch('/jobs')
      .then(response => {
        if (!response.ok) {
          throw new Error('Failed to load jobs');
        }
        return response.json();
      })
      .then(data => {
        if (!data || !Array.isArray(data.jobs)) {
          return;
        }
        const mapped = data.jobs
          .map(job => jobToEntry(job))
          .filter(entry => entry !== null)
          .sort((a, b) => {
            const left = a.startedAt ? a.startedAt.getTime() : 0;
            const right = b.startedAt ? b.startedAt.getTime() : 0;
            return right - left;
          });
        replaceDownloads(mapped);
        mapped
          .filter(entry => entry.status === 'processing' || entry.status === 'queued')
          .forEach(entry => beginJobPolling(entry.id));
      })
      .catch(() => {});
  }

  function ensureListPolling() {
    if (listRefreshTimer !== null) {
      return;
    }
    listRefreshTimer = setInterval(refreshJobs, LIST_REFRESH_INTERVAL);
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

    fetch('/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
      .then(response => response.json().then(data => ({ ok: response.ok, data })))
      .then(({ ok, data }) => {
        if (!ok) {
          if (consoleDiv) {
            consoleDiv.innerHTML = '';
          }
          const logs = Array.isArray(data.logs) ? data.logs : [];
          if (logs.length) {
            logs.forEach(line => {
              const isError = typeof line === 'string' && line.startsWith('ERROR');
              appendConsoleLine(line, isError);
            });
          } else {
            appendConsoleLine('ERROR: Request failed.', true);
          }
          return;
        }

        const job = data && data.job ? data.job : null;
        if (!job || !job.id) {
          appendConsoleLine('ERROR: No job information returned.', true);
          return;
        }

        activeJobId = job.id;
        showJobLogs(job);
        const entry = jobToEntry(job);
        if (entry) {
          upsertDownload(entry);
        }
        beginJobPolling(job.id, true);
      })
      .catch(err => {
        if (consoleDiv) {
          consoleDiv.innerHTML = '';
        }
        const message = err && err.message ? err.message : String(err);
        appendConsoleLine('ERROR: ' + message, true);
      });
  });

  updateExtraVisibility();
  refreshJobs();
  ensureListPolling();
  renderDownloads();
});
