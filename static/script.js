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
    extraCheckbox: document.getElementById('extra'),
    mergePlaylistCheckbox: document.getElementById('mergePlaylist'),
    extraTypeSelect: document.getElementById('extraType'),
    extraFields: document.getElementById('extraFields'),
    extraNameInput: document.getElementById('extra_name'),
    consoleDiv: document.getElementById('console'),
    downloadsList: document.getElementById('downloadsList'),
    copyButton: document.getElementById('copyLogButton')
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
  const POLL_INTERVAL = 1000;

  const initialDebugMode = document.body && document.body.dataset
    ? document.body.dataset.debugMode === 'true'
    : false;

  const state = {
    downloads: [],
    pollers: new Map(),
    debugMode: initialDebugMode,
    lastLogs: [],
    copyFeedbackTimeout: null
  };

  const IMPORTANT_LINE_SNIPPETS = [
    'success! video saved',
    'renaming downloaded file',
    'treating video as main video file',
    'storing video in subfolder',
    'created movie folder',
    'fetching radarr details',
    'resolved youtube format'
  ];

  const NOISY_WARNING_SNIPPETS = [
    '[youtube]',
    'sabr streaming',
    'web client https formats have been skipped',
    'web_safari client https formats have been skipped',
    'tv client https formats have been skipped'
  ];

  const COPY_BUTTON_DEFAULT_LABEL = 'Copy Full Log';

  function clearCopyFeedbackTimer() {
    if (state.copyFeedbackTimeout) {
      clearTimeout(state.copyFeedbackTimeout);
      state.copyFeedbackTimeout = null;
    }
  }

  function updateCopyButtonVisibility() {
    if (!elements.copyButton) {
      return;
    }
    clearCopyFeedbackTimer();
    elements.copyButton.textContent = COPY_BUTTON_DEFAULT_LABEL;
    if (state.debugMode) {
      elements.copyButton.removeAttribute('hidden');
      elements.copyButton.disabled = false;
    } else {
      elements.copyButton.setAttribute('hidden', 'hidden');
    }
  }

  function setDebugMode(enabled) {
    const value = Boolean(enabled);
    const changed = state.debugMode !== value;
    state.debugMode = value;
    if (document.body && document.body.dataset) {
      document.body.dataset.debugMode = value ? 'true' : 'false';
    }
    updateCopyButtonVisibility();
    if (changed && state.lastLogs && state.lastLogs.length) {
      renderLogLines(state.lastLogs);
    }
  }

  function shouldDisplayLogLine(line) {
    const original = typeof line === 'string' ? line : String(line ?? '');
    const trimmed = original.trim();
    if (!trimmed) {
      return false;
    }
    const lowered = trimmed.toLowerCase();
    if (lowered.startsWith('debug:')) {
      return false;
    }
    if (lowered.startsWith('warning:')) {
      return !NOISY_WARNING_SNIPPETS.some(snippet => lowered.includes(snippet));
    }
    if (lowered.startsWith('error:')) {
      return true;
    }
    if (lowered.startsWith('[download]') || lowered.startsWith('[ffmpeg]') || lowered.startsWith('[merger]')) {
      return true;
    }
    return IMPORTANT_LINE_SNIPPETS.some(snippet => lowered.includes(snippet));
  }

  function interpretLogLine(rawText, forcedType = null) {
    const original = typeof rawText === 'string' ? rawText : String(rawText ?? '');
    const trimmed = original.trim();
    let type = forcedType || 'info';
    let text = original;

    if (!forcedType) {
      const errorMatch = trimmed.match(/^ERROR:\s*(.*)$/i);
      if (errorMatch) {
        type = 'error';
        text = errorMatch[1] || 'Error';
        return { text, type };
      }
      const warningMatch = trimmed.match(/^WARNING:\s*(.*)$/i);
      if (warningMatch) {
        type = 'warning';
        text = warningMatch[1] || 'Warning';
        return { text, type };
      }
      const debugMatch = trimmed.match(/^DEBUG:\s*(.*)$/i);
      if (debugMatch) {
        type = 'debug';
        text = debugMatch[1] || 'Debug';
        return { text, type };
      }
      if (trimmed.startsWith('[download]')) {
        type = 'progress';
        text = trimmed;
        return { text, type };
      }
      if (trimmed.startsWith('[ffmpeg]')) {
        type = 'ffmpeg';
        text = trimmed;
        return { text, type };
      }
    }

    if (forcedType === 'muted') {
      type = 'muted';
    } else if (forcedType === 'error') {
      type = 'error';
      text = trimmed.replace(/^ERROR:\s*/i, '') || text;
    } else if (forcedType === 'warning') {
      type = 'warning';
      text = trimmed.replace(/^WARNING:\s*/i, '') || text;
    }

    return { text, type };
  }

  function appendConsoleLine(text, typeOverride = null) {
    if (!elements.consoleDiv) {
      return;
    }
    const lineElem = document.createElement('div');
    const { text: displayText, type } = interpretLogLine(text, typeOverride);
    lineElem.textContent = displayText;
    lineElem.classList.add('log-line', `log-${type}`);
    elements.consoleDiv.insertBefore(lineElem, elements.consoleDiv.firstChild);
    elements.consoleDiv.scrollTop = 0;
  }

  function resetConsole(message) {
    if (!elements.consoleDiv) {
      return;
    }
    elements.consoleDiv.innerHTML = '';
    state.lastLogs = [];
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
    state.lastLogs = entries.slice();
    const filtered = entries.filter(line => {
      if (state.debugMode) {
        return true;
      }
      return shouldDisplayLogLine(line);
    });
    if (!filtered.length) {
      if (entries.length && !state.debugMode) {
        appendConsoleLine(
          'Verbose output hidden. Enable debug mode in Settings to view full yt-dlp logs.',
          'muted'
        );
        return;
      }
      appendConsoleLine('No output yet.', 'muted');
      return;
    }
    filtered.forEach(line => {
      appendConsoleLine(line);
    });
  }

  async function copyFullLogToClipboard() {
    if (!elements.copyButton || !state.debugMode) {
      return;
    }
    const content = Array.isArray(state.lastLogs) ? state.lastLogs.join('\n').trim() : '';
    if (!content) {
      appendConsoleLine('No log output available to copy yet.', 'muted');
      return;
    }

    const handleSuccess = () => {
      elements.copyButton.textContent = 'Copied!';
      clearCopyFeedbackTimer();
      state.copyFeedbackTimeout = setTimeout(() => {
        elements.copyButton.textContent = COPY_BUTTON_DEFAULT_LABEL;
        state.copyFeedbackTimeout = null;
      }, 2000);
    };

    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(content);
        handleSuccess();
        return;
      }
    } catch (err) {
      // Fallback below
    }

    const textarea = document.createElement('textarea');
    textarea.value = content;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.top = '-9999px';
    document.body.appendChild(textarea);
    textarea.select();
    try {
      document.execCommand('copy');
      handleSuccess();
    } catch (err) {
      appendConsoleLine(`ERROR: Failed to copy log: ${err && err.message ? err.message : err}`, 'error');
    } finally {
      document.body.removeChild(textarea);
    }
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
      if (typeof data.debug_mode === 'boolean') {
        setDebugMode(data.debug_mode);
      }
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
      appendConsoleLine(`ERROR: Failed to poll job ${jobId}: ${err && err.message ? err.message : err}`, 'error');
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
      if (typeof data.debug_mode === 'boolean') {
        setDebugMode(data.debug_mode);
      }
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
      appendConsoleLine(`ERROR: Failed to load job history: ${err && err.message ? err.message : err}`, 'error');
    }
  }

  if (elements.movieNameInput) {
    elements.movieNameInput.addEventListener('input', syncMovieSelection);
    elements.movieNameInput.addEventListener('change', syncMovieSelection);
  }

  if (elements.extraCheckbox) {
    elements.extraCheckbox.addEventListener('change', updateExtraVisibility);
  }

  if (elements.copyButton) {
    elements.copyButton.addEventListener('click', copyFullLogToClipboard);
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
      extra: elements.extraCheckbox ? elements.extraCheckbox.checked : false,
      extraType: elements.extraTypeSelect ? elements.extraTypeSelect.value : 'trailer',
      extra_name: elements.extraNameInput ? elements.extraNameInput.value.trim() : '',
      merge_playlist: elements.mergePlaylistCheckbox ? elements.mergePlaylistCheckbox.checked : false
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
      errors.forEach(message => appendConsoleLine(`ERROR: ${message}`, 'error'));
      return;
    }

    try {
      const response = await fetch('/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await response.json().catch(() => ({}));
      if (typeof data.debug_mode === 'boolean') {
        setDebugMode(data.debug_mode);
      }

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
      appendConsoleLine(`ERROR: ${err && err.message ? err.message : err}`, 'error');
    }
  });

  setDebugMode(initialDebugMode);
  updateExtraVisibility();
  renderDownloads();
  loadInitialJobs();
});
