document.addEventListener('DOMContentLoaded', () => {
  const elements = {
    form: document.getElementById('movieForm'),
    ytInput: document.getElementById('yturl'),
    movieNameInput: document.getElementById('movieName'),
    movieOptions: document.getElementById('movieOptions'),
    movieIdInput: document.getElementById('movieId'),
    titleInput: document.getElementById('title'),
    yearInput: document.getElementById('year'),
    tmdbInput: document.getElementById('tmdb'),
    extensionSelect: document.getElementById('extension'),
    extraCheckbox: document.getElementById('extra'),
    extraTypeSelect: document.getElementById('extraType'),
    extraFields: document.getElementById('extraFields'),
    extraNameInput: document.getElementById('extra_name'),
    consoleDiv: document.getElementById('console'),
    downloadsList: document.getElementById('downloadsList')
  };

  if (!elements.form) {
    return;
  }

  const STATUS_LABELS = {
    queued: 'Queued',
    processing: 'Processing',
    complete: 'Completed',
    failed: 'Failed'
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
  const POLL_INTERVAL = 3000;

  const state = {
    downloads: [],
    pollers: new Map()
  };

  function appendConsoleLine(text, isError = false) {
    if (!elements.consoleDiv) {
      return;
    }
    const lineElem = document.createElement('div');
    lineElem.textContent = text;
    if (isError) {
      lineElem.classList.add('error-line');
    }
    elements.consoleDiv.appendChild(lineElem);
    elements.consoleDiv.scrollTop = elements.consoleDiv.scrollHeight;
  }

  function resetConsole(message) {
    if (!elements.consoleDiv) {
      return;
    }
    elements.consoleDiv.innerHTML = '';
    if (message) {
      appendConsoleLine(message);
    }
  }

  function renderLogLines(lines) {
    if (!elements.consoleDiv) {
      return;
    }
    elements.consoleDiv.innerHTML = '';
    const entries = Array.isArray(lines) ? lines : [];
    if (!entries.length) {
      appendConsoleLine('No output yet.');
      return;
    }
    entries.forEach(line => {
      const isError = typeof line === 'string' && line.trim().toUpperCase().startsWith('ERROR');
      appendConsoleLine(line, isError);
    });
  }

  function cleanErrorText(text) {
    if (!text) {
      return '';
    }
    return text.replace(/^ERROR:\s*/i, '').trim();
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

  function formatTime(value) {
    if (!value) {
      return '';
    }
    const date = value instanceof Date ? value : parseDate(value);
    if (!date) {
      return '';
    }
    try {
      return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch (err) {
      return date.toLocaleTimeString();
    }
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
      ? formatTime(entry.updatedAt || entry.completedAt)
      : formatTime(entry.startedAt);
    footerRight.textContent = timestamp;
    footer.appendChild(footerRight);

    wrapper.appendChild(footer);
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

  function stopJobPolling(jobId) {
    if (!jobId) {
      return;
    }
    const timer = state.pollers.get(jobId);
    if (timer) {
      clearInterval(timer);
      state.pollers.delete(jobId);
    }
  }

  async function pollJob(jobId, options = {}) {
    if (!jobId) {
      return;
    }
    try {
      const response = await fetch(`/jobs/${encodeURIComponent(jobId)}`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const data = await response.json();
      const job = data && data.job ? data.job : null;
      if (!job) {
        stopJobPolling(jobId);
        return;
      }
      const entry = normaliseJob(job);
      if (entry) {
        upsertDownload(entry);
      }
      if (options.showConsole && Array.isArray(job.logs)) {
        renderLogLines(job.logs);
      }
      if (job.status === 'complete' || job.status === 'failed') {
        stopJobPolling(jobId);
      }
    } catch (err) {
      appendConsoleLine(`ERROR: Failed to poll job ${jobId}: ${err && err.message ? err.message : err}`, true);
      stopJobPolling(jobId);
    }
  }

  function startJobPolling(jobId, options = {}) {
    if (!jobId || state.pollers.has(jobId)) {
      return;
    }
    pollJob(jobId, options);
    const timer = setInterval(() => pollJob(jobId, options), POLL_INTERVAL);
    state.pollers.set(jobId, timer);
  }

  function upsertDownload(update) {
    if (!update || !update.id) {
      return;
    }
    const index = state.downloads.findIndex(item => item.id === update.id);
    if (index >= 0) {
      const existing = state.downloads[index];
      state.downloads[index] = {
        ...existing,
        ...update,
        metadata: update.metadata || existing.metadata,
        subtitle: update.subtitle !== undefined ? update.subtitle : existing.subtitle,
        message: update.message !== undefined ? update.message : existing.message,
        progress: update.progress !== undefined ? update.progress : existing.progress,
        startedAt: update.startedAt || existing.startedAt,
        updatedAt: update.updatedAt || new Date()
      };
    } else {
      const now = new Date();
      state.downloads.push({
        metadata: [],
        subtitle: '',
        message: '',
        progress: typeof update.progress === 'number' ? update.progress : 0,
        ...update,
        startedAt: update.startedAt || now,
        updatedAt: update.updatedAt || now
      });
    }

    state.downloads.sort((a, b) => {
      const left = (a.startedAt instanceof Date ? a.startedAt : parseDate(a.startedAt)) || new Date(0);
      const right = (b.startedAt instanceof Date ? b.startedAt : parseDate(b.startedAt)) || new Date(0);
      return right.getTime() - left.getTime();
    });

    if (state.downloads.length > MAX_DOWNLOAD_ENTRIES) {
      const removed = state.downloads.splice(MAX_DOWNLOAD_ENTRIES);
      removed.forEach(entry => stopJobPolling(entry.id));
    }

    renderDownloads();
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
    const updatedAt = parseDate(job.updated_at || job.completed_at || job.started_at || job.created_at) || startedAt;
    const metadata = Array.isArray(job.metadata) ? job.metadata : [];
    return {
      id: job.id,
      label: job.label || 'Radarr Download',
      subtitle: job.subtitle || '',
      status,
      progress: typeof job.progress === 'number' ? Math.max(0, Math.min(100, job.progress)) : 0,
      metadata,
      message: job.message || '',
      startedAt,
      updatedAt
    };
  }

  function syncMovieSelection() {
    if (!elements.movieNameInput || !elements.movieOptions) {
      return;
    }
    const inputVal = elements.movieNameInput.value.trim();
    const options = Array.from(elements.movieOptions.options || []);
    const matchedOption = options.find(option => option.value === inputVal) || null;

    if (matchedOption) {
      if (elements.movieIdInput) {
        elements.movieIdInput.value = matchedOption.getAttribute('data-id') || '';
      }
      if (elements.titleInput) {
        elements.titleInput.value = matchedOption.getAttribute('data-title') || '';
      }
      if (elements.yearInput) {
        elements.yearInput.value = matchedOption.getAttribute('data-year') || '';
      }
      if (elements.tmdbInput) {
        elements.tmdbInput.value = matchedOption.getAttribute('data-tmdb') || '';
      }
    } else {
      if (elements.movieIdInput) elements.movieIdInput.value = '';
      if (elements.titleInput) elements.titleInput.value = '';
      if (elements.yearInput) elements.yearInput.value = '';
      if (elements.tmdbInput) elements.tmdbInput.value = '';
    }
  }

  function updateExtraVisibility() {
    if (!elements.extraFields || !elements.extraCheckbox || !elements.extraNameInput || !elements.extraTypeSelect) {
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

  async function loadInitialJobs() {
    try {
      const response = await fetch('/jobs');
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const data = await response.json();
      const jobs = Array.isArray(data.jobs) ? data.jobs : [];
      jobs.forEach(job => {
        const entry = normaliseJob(job);
        if (entry) {
          upsertDownload(entry);
        }
        if (job.status === 'queued' || job.status === 'processing') {
          startJobPolling(job.id);
        }
      });
    } catch (err) {
      appendConsoleLine(`ERROR: Failed to load job history: ${err && err.message ? err.message : err}`, true);
    }
  }

  if (elements.movieNameInput) {
    elements.movieNameInput.addEventListener('input', syncMovieSelection);
    elements.movieNameInput.addEventListener('change', syncMovieSelection);
  }

  if (elements.extraCheckbox) {
    elements.extraCheckbox.addEventListener('change', updateExtraVisibility);
  }

  elements.form.addEventListener('submit', async event => {
    event.preventDefault();

    const payload = {
      yturl: elements.ytInput ? elements.ytInput.value.trim() : '',
      movieName: elements.movieNameInput ? elements.movieNameInput.value.trim() : '',
      movieId: elements.movieIdInput ? elements.movieIdInput.value.trim() : '',
      title: elements.titleInput ? elements.titleInput.value.trim() : '',
      year: elements.yearInput ? elements.yearInput.value.trim() : '',
      tmdb: elements.tmdbInput ? elements.tmdbInput.value.trim() : '',
      extension: elements.extensionSelect ? elements.extensionSelect.value : 'mp4',
      extra: elements.extraCheckbox ? elements.extraCheckbox.checked : false,
      extraType: elements.extraTypeSelect ? elements.extraTypeSelect.value : 'trailer',
      extra_name: elements.extraNameInput ? elements.extraNameInput.value.trim() : ''
    };

    resetConsole('Submitting request...');

    const errors = [];
    if (!payload.yturl) {
      errors.push('YouTube URL is required.');
    } else {
      const ytPattern = /^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.be)\//i;
      if (!ytPattern.test(payload.yturl)) {
        errors.push('Please enter a valid YouTube URL.');
      }
    }
    if (!payload.movieId) {
      errors.push('Please select a valid movie from the list.');
    }
    if (payload.extra && !payload.extra_name) {
      errors.push('Please provide an extra name.');
    }

    if (errors.length) {
      errors.forEach(message => appendConsoleLine(`ERROR: ${message}`, true));
      return;
    }

    try {
      const response = await fetch('/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        const logs = Array.isArray(data.logs) ? data.logs : [];
        if (logs.length) {
          renderLogLines(logs);
        } else {
          renderLogLines(['ERROR: Request failed.']);
        }
        throw new Error(cleanErrorText(logs[0] || 'Request failed.'));
      }

      const job = data && data.job ? data.job : null;
      if (!job || !job.id) {
        renderLogLines(['ERROR: No job information returned.']);
        throw new Error('No job information returned.');
      }

      const logs = Array.isArray(job.logs) ? job.logs : ['Job queued.'];
      renderLogLines(logs);

      const entry = normaliseJob(job);
      if (entry) {
        upsertDownload(entry);
      }
      startJobPolling(job.id, { showConsole: true });
    } catch (err) {
      appendConsoleLine(`ERROR: ${err && err.message ? err.message : err}`, true);
    }
  });

  updateExtraVisibility();
  renderDownloads();
  loadInitialJobs();
});
