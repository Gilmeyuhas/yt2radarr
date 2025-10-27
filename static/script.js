document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('movieForm');
  if (!form) {
    return;
  }

  const elements = {
    ytInput: document.getElementById('yturl'),
    movieNameInput: document.getElementById('movieName'),
    movieOptions: document.getElementById('movieOptions'),
    movieIdInput: document.getElementById('movieId'),
    titleInput: document.getElementById('title'),
    yearInput: document.getElementById('year'),
    tmdbInput: document.getElementById('tmdb'),
    resolutionSelect: document.getElementById('resolution'),
    extensionSelect: document.getElementById('extension'),
    extraCheckbox: document.getElementById('extra'),
    extraTypeSelect: document.getElementById('extraType'),
    extraFields: document.getElementById('extraFields'),
    extraNameInput: document.getElementById('extra_name'),
    console: document.getElementById('console'),
    downloadsList: document.getElementById('downloadsList'),
  };

  const STATUS_LABELS = {
    queued: 'Queued',
    processing: 'Processing',
    complete: 'Completed',
    failed: 'Failed',
  };

  const MAX_DOWNLOAD_ENTRIES = 8;
  const LIST_REFRESH_INTERVAL = 8000;
  const JOB_POLL_INTERVAL = 3000;

  const state = {
    downloads: [],
    selectedJobId: null,
    pollers: new Map(),
    listTimer: null,
  };

  function appendConsoleLine(text, isError = false) {
    if (!elements.console) {
      return;
    }
    const line = document.createElement('div');
    if (isError) {
      line.classList.add('error-line');
    }
    line.textContent = text;
    elements.console.appendChild(line);
    elements.console.scrollTop = elements.console.scrollHeight;
  }

  function resetConsole(message) {
    if (!elements.console) {
      return;
    }
    elements.console.innerHTML = '';
    if (message) {
      appendConsoleLine(message);
    }
  }

  function renderLogLines(lines) {
    resetConsole();
    if (!elements.console) {
      return;
    }
    if (!Array.isArray(lines) || !lines.length) {
      appendConsoleLine('No output yet.');
      return;
    }
    lines.forEach(line => {
      const text = typeof line === 'string' ? line : String(line);
      const isError = text.startsWith('ERROR');
      appendConsoleLine(text, isError);
    });
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
    wrapper.dataset.jobId = entry.id;

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

    if (state.selectedJobId === entry.id) {
      wrapper.classList.add('is-active');
    }

    return wrapper;
  }

  function renderDownloads() {
    if (!elements.downloadsList) {
      return;
    }
    elements.downloadsList.innerHTML = '';
    if (!state.downloads.length) {
      elements.downloadsList.classList.add('empty');
      const empty = document.createElement('div');
      empty.className = 'empty-state';
      empty.textContent = 'No downloads yet.';
      elements.downloadsList.appendChild(empty);
      return;
    }

    elements.downloadsList.classList.remove('empty');
    state.downloads.forEach(entry => {
      elements.downloadsList.appendChild(buildDownloadItem(entry));
    });
  }

  function setDownloads(entries) {
    state.downloads = entries.slice(0, MAX_DOWNLOAD_ENTRIES);
    renderDownloads();
  }

  function upsertDownload(entry, { position = 'start' } = {}) {
    if (!entry || !entry.id) {
      return;
    }
    const index = state.downloads.findIndex(item => item.id === entry.id);
    if (index >= 0) {
      const existing = state.downloads[index];
      const merged = {
        ...existing,
        ...entry,
        metadata: entry.metadata !== undefined ? entry.metadata : existing.metadata,
        subtitle: entry.subtitle !== undefined ? entry.subtitle : existing.subtitle,
        message: entry.message !== undefined ? entry.message : existing.message,
        progress: entry.progress !== undefined ? entry.progress : existing.progress,
        startedAt: existing.startedAt,
        updatedAt: entry.updatedAt || new Date(),
      };
      state.downloads[index] = merged;
    } else {
      const record = {
        metadata: [],
        subtitle: '',
        message: '',
        progress: 0,
        ...entry,
        progress: entry.progress !== undefined ? entry.progress : 0,
        startedAt: entry.startedAt || new Date(),
        updatedAt: entry.updatedAt || new Date(),
      };
      if (position === 'end') {
        state.downloads.push(record);
      } else {
        state.downloads.unshift(record);
      }
      if (state.downloads.length > MAX_DOWNLOAD_ENTRIES) {
        state.downloads.length = MAX_DOWNLOAD_ENTRIES;
      }
    }
    renderDownloads();
  }

  function stopJobPolling(jobId) {
    const info = state.pollers.get(jobId);
    if (info && info.timer) {
      clearInterval(info.timer);
    }
    state.pollers.delete(jobId);
  }

  function normaliseJob(job) {
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
      updatedAt,
    };
  }

  async function fetchJson(url, options) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data && data.error ? data.error : `Request failed: ${response.status}`);
      error.response = data;
      throw error;
    }
    return data;
  }

  async function pollJob(jobId, { showConsole = false } = {}) {
    if (!jobId) {
      return;
    }
    const info = state.pollers.get(jobId);
    const shouldShow = showConsole || (info && info.showConsole) || state.selectedJobId === jobId;
    try {
      const data = await fetchJson(`/jobs/${encodeURIComponent(jobId)}`);
      if (!data || !data.job) {
        return;
      }
      const entry = normaliseJob(data.job);
      if (entry) {
        upsertDownload(entry);
        if (entry.status === 'complete' || entry.status === 'failed') {
          stopJobPolling(jobId);
        }
      }
      if (shouldShow) {
        state.selectedJobId = jobId;
        renderDownloads();
        renderLogLines(data.job.logs || []);
      }
    } catch (err) {
      if (err && err.response && err.response.error === 'Job not found.') {
        stopJobPolling(jobId);
      }
    }
  }

  function beginJobPolling(jobId, { showConsole = false } = {}) {
    if (!jobId) {
      return;
    }
    const existing = state.pollers.get(jobId);
    if (existing) {
      if (showConsole) {
        existing.showConsole = true;
      }
      return;
    }
    const info = {
      timer: null,
      showConsole,
    };
    const tick = () => {
      pollJob(jobId);
    };
    info.timer = setInterval(tick, JOB_POLL_INTERVAL);
    state.pollers.set(jobId, info);
    pollJob(jobId, { showConsole });
  }

  async function refreshJobs() {
    try {
      const data = await fetchJson('/jobs');
      if (!data || !Array.isArray(data.jobs)) {
        return;
      }
      const mapped = data.jobs
        .map(job => normaliseJob(job))
        .filter(entry => entry !== null)
        .sort((a, b) => {
          const left = a.startedAt ? a.startedAt.getTime() : 0;
          const right = b.startedAt ? b.startedAt.getTime() : 0;
          return right - left;
        });
      setDownloads(mapped);
      mapped
        .filter(entry => entry.status === 'processing' || entry.status === 'queued')
        .forEach(entry => beginJobPolling(entry.id));
      if (state.selectedJobId && !state.downloads.some(item => item.id === state.selectedJobId)) {
        state.selectedJobId = null;
        renderDownloads();
      }
    } catch (err) {
      // Ignore refresh failures to avoid spamming the console.
    }
  }

  function ensureListPolling() {
    if (state.listTimer !== null) {
      return;
    }
    state.listTimer = setInterval(refreshJobs, LIST_REFRESH_INTERVAL);
  }

  function selectJob(jobId) {
    if (!jobId) {
      return;
    }
    state.selectedJobId = jobId;
    renderDownloads();
    beginJobPolling(jobId, { showConsole: true });
    pollJob(jobId, { showConsole: true });
  }

  function updateExtraVisibility() {
    if (!elements.extraCheckbox || !elements.extraFields || !elements.extraNameInput || !elements.extraTypeSelect) {
      return;
    }
    if (elements.extraCheckbox.checked) {
      elements.extraFields.style.display = 'block';
      elements.extraNameInput.required = true;
    } else {
      elements.extraFields.style.display = 'none';
      elements.extraNameInput.required = false;
      elements.extraNameInput.value = '';
      elements.extraTypeSelect.value = 'trailer';
    }
  }

  function syncMovieSelection() {
    if (!elements.movieNameInput || !elements.movieOptions) {
      return;
    }
    const inputVal = elements.movieNameInput.value;
    let matched = false;
    for (let i = 0; i < elements.movieOptions.options.length; i += 1) {
      const option = elements.movieOptions.options[i];
      if (option.value === inputVal) {
        matched = true;
        if (elements.movieIdInput) {
          elements.movieIdInput.value = option.getAttribute('data-id') || '';
        }
        if (elements.titleInput) {
          elements.titleInput.value = option.getAttribute('data-title') || '';
        }
        if (elements.yearInput) {
          elements.yearInput.value = option.getAttribute('data-year') || '';
        }
        if (elements.tmdbInput) {
          elements.tmdbInput.value = option.getAttribute('data-tmdb') || '';
        }
        break;
      }
    }
    if (!matched) {
      if (elements.movieIdInput) elements.movieIdInput.value = '';
      if (elements.titleInput) elements.titleInput.value = '';
      if (elements.yearInput) elements.yearInput.value = '';
      if (elements.tmdbInput) elements.tmdbInput.value = '';
    }
  }

  if (elements.extraCheckbox) {
    elements.extraCheckbox.addEventListener('change', updateExtraVisibility);
  }

  if (elements.movieNameInput) {
    elements.movieNameInput.addEventListener('input', syncMovieSelection);
  }

  if (elements.downloadsList) {
    elements.downloadsList.addEventListener('click', event => {
      const target = event.target.closest('.download-item');
      if (!target || !target.dataset.jobId) {
        return;
      }
      selectJob(target.dataset.jobId);
    });
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();

    const payload = {
      yturl: elements.ytInput ? elements.ytInput.value.trim() : '',
      movieName: elements.movieNameInput ? elements.movieNameInput.value.trim() : '',
      movieId: elements.movieIdInput ? elements.movieIdInput.value.trim() : '',
      title: elements.titleInput ? elements.titleInput.value.trim() : '',
      year: elements.yearInput ? elements.yearInput.value.trim() : '',
      tmdb: elements.tmdbInput ? elements.tmdbInput.value.trim() : '',
      resolution: elements.resolutionSelect ? elements.resolutionSelect.value : 'best',
      extension: elements.extensionSelect ? elements.extensionSelect.value : 'mp4',
      extra: elements.extraCheckbox ? elements.extraCheckbox.checked : false,
      extraType: elements.extraTypeSelect ? elements.extraTypeSelect.value : 'trailer',
      extra_name: elements.extraNameInput ? elements.extraNameInput.value.trim() : '',
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

    try {
      const data = await fetchJson('/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const job = data && data.job ? data.job : null;
      if (!job || !job.id) {
        appendConsoleLine('ERROR: No job information returned.', true);
        return;
      }

      state.selectedJobId = job.id;
      renderDownloads();
      renderLogLines(job.logs || []);

      const entry = normaliseJob(job);
      if (entry) {
        upsertDownload(entry);
      }
      beginJobPolling(job.id, { showConsole: true });
    } catch (err) {
      if (err && err.response && Array.isArray(err.response.logs)) {
        renderLogLines(err.response.logs);
      } else {
        appendConsoleLine(`ERROR: ${err && err.message ? err.message : err}`, true);
      }
    }
  });

  updateExtraVisibility();
  syncMovieSelection();
  refreshJobs();
  ensureListPolling();
  renderDownloads();
});

