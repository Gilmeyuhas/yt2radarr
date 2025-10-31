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
    playlistModeSelect: document.getElementById('playlistMode'),
    playlistExtrasFields: document.getElementById('playlistExtrasFields'),
    extraTypeSelect: document.getElementById('extraType'),
    extraFields: document.getElementById('extraFields'),
    extraNameInput: document.getElementById('extra_name'),
    playlistFetchButton: document.getElementById('playlistFetchButton'),
    playlistClearButton: document.getElementById('playlistClearButton'),
    playlistPreview: document.getElementById('playlistPreview'),
    playlistPreviewTitle: document.getElementById('playlistPreviewTitle'),
    playlistPreviewMeta: document.getElementById('playlistPreviewMeta'),
    playlistEntriesList: document.getElementById('playlistEntriesList'),
    playlistPreviewPlaceholder: document.getElementById('playlistPreviewPlaceholder'),
    playlistPreviewError: document.getElementById('playlistPreviewError'),
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
    copyFeedbackTimeout: null,
    playlistEntries: [],
    playlistTitle: '',
    playlistTotalCount: 0,
    playlistTruncated: false,
    playlistLoading: false,
    playlistPlaceholderText: elements.playlistPreviewPlaceholder
      ? elements.playlistPreviewPlaceholder.textContent
      : ''
  };

  const IMPORTANT_LINE_SNIPPETS = [
    'success! video saved',
    'renaming downloaded file',
    'treating video as main video file',
    'storing video in subfolder',
    'created movie folder',
    'fetching radarr details',
    'resolved youtube format',
    'saving playlist extra'
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

  function setPlaylistLoading(isLoading) {
    state.playlistLoading = Boolean(isLoading);
    if (elements.playlistFetchButton) {
      elements.playlistFetchButton.disabled = state.playlistLoading;
      const hasEntries = state.playlistEntries && state.playlistEntries.length > 0;
      if (state.playlistLoading) {
        elements.playlistFetchButton.textContent = 'Loading…';
      } else {
        elements.playlistFetchButton.textContent = hasEntries
          ? 'Reload Playlist'
          : 'Load Playlist Details';
      }
    }
  }

  function clearPlaylistEntries({ keepPlaceholder = false } = {}) {
    state.playlistEntries = [];
    state.playlistTitle = '';
    state.playlistTotalCount = 0;
    state.playlistTruncated = false;
    state.playlistLoading = false;
    if (elements.playlistEntriesList) {
      elements.playlistEntriesList.innerHTML = '';
    }
    if (elements.playlistPreviewTitle) {
      elements.playlistPreviewTitle.textContent = '';
    }
    if (elements.playlistPreviewMeta) {
      elements.playlistPreviewMeta.textContent = '';
    }
    if (elements.playlistPreviewError) {
      elements.playlistPreviewError.textContent = '';
      elements.playlistPreviewError.setAttribute('hidden', 'hidden');
    }
    if (elements.playlistPreview) {
      elements.playlistPreview.setAttribute('hidden', 'hidden');
    }
    if (elements.playlistPreviewPlaceholder) {
      if (!keepPlaceholder && state.playlistPlaceholderText) {
        elements.playlistPreviewPlaceholder.textContent = state.playlistPlaceholderText;
      }
      elements.playlistPreviewPlaceholder.removeAttribute('hidden');
    }
    if (elements.playlistClearButton) {
      elements.playlistClearButton.setAttribute('hidden', 'hidden');
    }
    setPlaylistLoading(false);
  }

  function renderPlaylistEntries() {
    const hasEntries = state.playlistEntries && state.playlistEntries.length > 0;
    if (elements.playlistPreviewPlaceholder) {
      if (hasEntries) {
        elements.playlistPreviewPlaceholder.setAttribute('hidden', 'hidden');
      } else if (!state.playlistLoading) {
        elements.playlistPreviewPlaceholder.removeAttribute('hidden');
      }
    }
    if (elements.playlistPreview) {
      if (hasEntries) {
        elements.playlistPreview.removeAttribute('hidden');
      } else {
        elements.playlistPreview.setAttribute('hidden', 'hidden');
      }
    }
    if (elements.playlistClearButton) {
      if (hasEntries) {
        elements.playlistClearButton.removeAttribute('hidden');
      } else {
        elements.playlistClearButton.setAttribute('hidden', 'hidden');
      }
    }

    if (elements.playlistPreviewTitle) {
      elements.playlistPreviewTitle.textContent = state.playlistTitle || '';
    }

    if (elements.playlistPreviewMeta) {
      const entryCount = state.playlistEntries.length;
      const totalCount = state.playlistTotalCount || entryCount;
      if (hasEntries) {
        let summary = `${entryCount} video${entryCount === 1 ? '' : 's'}`;
        if (state.playlistTruncated && totalCount > entryCount) {
          summary = `${entryCount} of ${totalCount} videos`;
        }
        elements.playlistPreviewMeta.textContent = summary;
      } else {
        elements.playlistPreviewMeta.textContent = '';
      }
    }

    if (!elements.playlistEntriesList) {
      return;
    }

    elements.playlistEntriesList.innerHTML = '';
    if (!hasEntries) {
      return;
    }

    const fragment = document.createDocumentFragment();
    state.playlistEntries.forEach(entry => {
      const row = document.createElement('div');
      row.classList.add('playlist-entry-row');
      row.dataset.entryIndex = String(entry.index);

      const infoColumn = document.createElement('div');
      infoColumn.classList.add('playlist-entry-info');
      const indexBadge = document.createElement('div');
      indexBadge.classList.add('playlist-entry-index');
      indexBadge.textContent = `#${String(entry.index).padStart(2, '0')}`;
      const titleLine = document.createElement('div');
      titleLine.classList.add('playlist-entry-title');
      titleLine.textContent = entry.title || `Entry ${entry.index}`;
      if (entry.duration_text) {
        const duration = document.createElement('span');
        duration.classList.add('playlist-entry-duration');
        duration.textContent = entry.duration_text;
        titleLine.appendChild(duration);
      }
      infoColumn.appendChild(indexBadge);
      infoColumn.appendChild(titleLine);

      const controlsColumn = document.createElement('div');
      controlsColumn.classList.add('playlist-entry-controls');
      const typeSelect = document.createElement('select');
      typeSelect.classList.add('playlist-entry-type');
      typeSelect.dataset.entryIndex = String(entry.index);
      Object.entries(EXTRA_TYPE_LABELS).forEach(([value, label]) => {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = label;
        typeSelect.appendChild(option);
      });
      if (entry.type && EXTRA_TYPE_LABELS[entry.type]) {
        typeSelect.value = entry.type;
      }
      typeSelect.addEventListener('change', event => {
        const indexValue = parseInt(event.target.dataset.entryIndex, 10);
        const selected = event.target.value;
        if (Number.isFinite(indexValue)) {
          const targetEntry = state.playlistEntries.find(item => item.index === indexValue);
          if (targetEntry) {
            targetEntry.type = selected;
          }
        }
      });

      const nameInput = document.createElement('input');
      nameInput.type = 'text';
      nameInput.placeholder = 'Custom name (optional)';
      nameInput.classList.add('playlist-entry-name');
      nameInput.dataset.entryIndex = String(entry.index);
      nameInput.value = entry.name || '';
      nameInput.addEventListener('input', event => {
        const indexValue = parseInt(event.target.dataset.entryIndex, 10);
        if (Number.isFinite(indexValue)) {
          const targetEntry = state.playlistEntries.find(item => item.index === indexValue);
          if (targetEntry) {
            targetEntry.name = event.target.value;
          }
        }
      });

      controlsColumn.appendChild(typeSelect);
      controlsColumn.appendChild(nameInput);

      row.appendChild(infoColumn);
      row.appendChild(controlsColumn);
      fragment.appendChild(row);
    });

    elements.playlistEntriesList.appendChild(fragment);
  }

  function showPlaylistError(message) {
    if (!elements.playlistPreviewError) {
      return;
    }
    const text = typeof message === 'string' ? message.trim() : '';
    elements.playlistPreviewError.textContent = text || 'Failed to load playlist details.';
    elements.playlistPreviewError.removeAttribute('hidden');
  }

  function hidePlaylistError() {
    if (elements.playlistPreviewError) {
      elements.playlistPreviewError.textContent = '';
      elements.playlistPreviewError.setAttribute('hidden', 'hidden');
    }
  }

  async function fetchPlaylistPreview() {
    if (!elements.playlistModeSelect || elements.playlistModeSelect.value !== 'extras') {
      updatePlaylistControls();
      return;
    }

    const url = elements.ytInput ? elements.ytInput.value.trim() : '';
    if (!url) {
      const message = 'Enter a YouTube playlist URL to load entries.';
      appendConsoleLine(`ERROR: ${message}`, 'error');
      showPlaylistError(message);
      return;
    }

    setPlaylistLoading(true);
    hidePlaylistError();

    try {
      const response = await fetch('/playlist_preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ yturl: url })
      });
      const data = await response.json().catch(() => ({}));
      if (typeof data.debug_mode === 'boolean') {
        setDebugMode(data.debug_mode);
      }

      if (!response.ok) {
        const errorMessage = (data && data.error ? data.error : `HTTP ${response.status}`).trim();
        showPlaylistError(errorMessage);
        clearPlaylistEntries({ keepPlaceholder: true });
        appendConsoleLine(`ERROR: ${errorMessage}`, 'error');
        return;
      }

      const rawEntries = Array.isArray(data.entries) ? data.entries : [];
      const totalCount = typeof data.total_count === 'number' ? data.total_count : rawEntries.length;
      const truncated = Boolean(data.truncated) && totalCount > rawEntries.length;
      const playlistTitle = typeof data.playlist_title === 'string' ? data.playlist_title.trim() : '';

      const previousEntries = new Map();
      state.playlistEntries.forEach(entry => {
        const key = (entry.id || '').toLowerCase() || (entry.title || '').toLowerCase();
        if (!previousEntries.has(key)) {
          previousEntries.set(key, entry);
        }
      });

      const normalisedEntries = [];
      rawEntries.forEach(rawEntry => {
        if (!rawEntry || typeof rawEntry !== 'object') {
          return;
        }
        let indexValue = parseInt(rawEntry.index ?? rawEntry.playlist_index ?? rawEntry.order, 10);
        if (!Number.isFinite(indexValue) || indexValue < 1) {
          indexValue = normalisedEntries.length + 1;
        }
        const titleValue = (rawEntry.title || '').toString().trim() || `Entry ${indexValue}`;
        const idValue = (rawEntry.id || '').toString().trim();
        const key = idValue.toLowerCase() || titleValue.toLowerCase();
        const existing = previousEntries.get(key);
        const defaultType = indexValue === 1 ? 'trailer' : 'other';
        const typeValue = existing && EXTRA_TYPE_LABELS[existing.type] ? existing.type : defaultType;
        const nameValue = existing && typeof existing.name === 'string' ? existing.name : '';

        const entryDuration = typeof rawEntry.duration === 'number' ? rawEntry.duration : null;
        const durationText = typeof rawEntry.duration_text === 'string' ? rawEntry.duration_text : '';

        normalisedEntries.push({
          index: indexValue,
          id: idValue,
          title: titleValue,
          duration: entryDuration,
          duration_text: durationText,
          type: EXTRA_TYPE_LABELS[typeValue] ? typeValue : defaultType,
          name: nameValue
        });
      });

      normalisedEntries.sort((a, b) => a.index - b.index);
      normalisedEntries.forEach((entry, position) => {
        entry.index = position + 1;
      });

      state.playlistEntries = normalisedEntries;
      state.playlistTitle = playlistTitle;
      state.playlistTotalCount = totalCount;
      state.playlistTruncated = truncated;

      if (!normalisedEntries.length && elements.playlistPreviewPlaceholder) {
        elements.playlistPreviewPlaceholder.textContent = 'This playlist does not contain any videos.';
      }

      renderPlaylistEntries();
      if (!normalisedEntries.length) {
        hidePlaylistError();
      }
    } catch (err) {
      const message = err && err.message ? err.message : err;
      showPlaylistError(`Failed to load playlist: ${message}`);
      appendConsoleLine(`ERROR: Failed to load playlist: ${message}`, 'error');
      clearPlaylistEntries({ keepPlaceholder: true });
    } finally {
      setPlaylistLoading(false);
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

  function updatePlaylistControls() {
    const extraEnabled = elements.extraCheckbox ? elements.extraCheckbox.checked : false;
    if (elements.playlistModeSelect) {
      const extrasOption = elements.playlistModeSelect.querySelector('option[value="extras"]');
      if (extrasOption) {
        extrasOption.disabled = !extraEnabled;
      }
      if (!extraEnabled && elements.playlistModeSelect.value === 'extras') {
        elements.playlistModeSelect.value = 'single';
      }
    }

    const requiresPlaylistExtras =
      extraEnabled && elements.playlistModeSelect && elements.playlistModeSelect.value === 'extras';

    if (elements.playlistExtrasFields) {
      elements.playlistExtrasFields.style.display = requiresPlaylistExtras ? 'block' : 'none';
    }

    if (requiresPlaylistExtras) {
      renderPlaylistEntries();
    } else if (elements.playlistFetchButton) {
      elements.playlistFetchButton.textContent = 'Load Playlist Details';
      elements.playlistFetchButton.disabled = false;
    }
  }

  function updateExtraVisibility() {
    if (!elements.extraFields || !elements.extraCheckbox || !elements.extraNameInput || !elements.extraTypeSelect) {
      return;
    }
    const playlistMode = elements.playlistModeSelect ? elements.playlistModeSelect.value : 'single';
    if (elements.extraCheckbox.checked) {
      elements.extraFields.style.display = 'block';
      const requiresExtraName = playlistMode !== 'extras';
      elements.extraNameInput.required = requiresExtraName;
      if (!requiresExtraName) {
        elements.extraNameInput.value = '';
      }
    } else {
      elements.extraFields.style.display = 'none';
      elements.extraNameInput.required = false;
      elements.extraNameInput.value = '';
      elements.extraTypeSelect.value = 'trailer';
    }

    updatePlaylistControls();
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

  if (elements.playlistModeSelect) {
    elements.playlistModeSelect.addEventListener('change', () => {
      updateExtraVisibility();
    });
  }

  if (elements.extraCheckbox) {
    elements.extraCheckbox.addEventListener('change', updateExtraVisibility);
  }

  if (elements.ytInput) {
    elements.ytInput.addEventListener('change', () => {
      if (state.playlistEntries.length) {
        clearPlaylistEntries();
        renderPlaylistEntries();
      }
    });
  }

  if (elements.playlistFetchButton) {
    elements.playlistFetchButton.addEventListener('click', () => {
      if (!state.playlistLoading) {
        fetchPlaylistPreview();
      }
    });
  }

  if (elements.playlistClearButton) {
    elements.playlistClearButton.addEventListener('click', () => {
      clearPlaylistEntries();
      renderPlaylistEntries();
    });
  }

  if (elements.copyButton) {
    elements.copyButton.addEventListener('click', copyFullLogToClipboard);
  }

  elements.form.addEventListener('submit', async event => {
    event.preventDefault();

    const playlistMode = elements.playlistModeSelect ? elements.playlistModeSelect.value : 'single';
    const playlistExtraEntries =
      playlistMode === 'extras'
        ? state.playlistEntries.map(entry => ({
            index: entry.index,
            id: entry.id || '',
            title: entry.title || '',
            duration: typeof entry.duration === 'number' ? entry.duration : null,
            type: entry.type || 'other',
            name: entry.name || ''
          }))
        : [];
    const playlistExtraTypes = playlistExtraEntries
      .map(entry => (entry.type || '').trim())
      .filter(value => value.length > 0);

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
      playlist_mode: playlistMode,
      merge_playlist: playlistMode === 'merge',
      playlist_extra_types: playlistExtraTypes,
      playlist_extra_entries: playlistExtraEntries
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
    if (payload.extra && !payload.extra_name && playlistMode !== 'extras') {
      errors.push('Please provide an extra name.');
    }

    if (playlistMode === 'extras' && !payload.extra) {
      errors.push('Playlist extras require the "Store in subfolder" option.');
    }

    if (playlistMode === 'extras' && playlistExtraEntries.length === 0) {
      errors.push('Please load the playlist and configure at least one entry.');
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
  renderPlaylistEntries();
  renderDownloads();
  loadInitialJobs();
});
