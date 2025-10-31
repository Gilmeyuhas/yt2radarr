document.addEventListener('DOMContentLoaded', () => {
  const elements = {
    form: document.getElementById('movieForm'),
    ytInput: document.getElementById('yturl'),
    ytSearchInput: document.getElementById('ytSearch'),
    ytSearchButton: document.getElementById('ytSearchButton'),
    ytSearchFeedback: document.getElementById('ytSearchFeedback'),
    ytSearchResults: document.getElementById('ytSearchResults'),
    movieNameInput: document.getElementById('movieName'),
    movieOptions: document.getElementById('movieOptions'),
    movieIdInput: document.getElementById('movieId'),
    titleInput: document.getElementById('title'),
    yearInput: document.getElementById('year'),
    tmdbInput: document.getElementById('tmdb'),
    extraCheckbox: document.getElementById('extra'),
    playlistModeSelect: document.getElementById('playlistMode'),
    extraTypeSelect: document.getElementById('extraType'),
    extraFields: document.getElementById('extraFields'),
    extraNameInput: document.getElementById('extra_name'),
    consoleDiv: document.getElementById('console'),
    downloadsList: document.getElementById('downloadsList'),
    copyButton: document.getElementById('copyLogButton'),
    movieFeedback: document.getElementById('movieFeedback'),
    movieNotFoundPrompt: document.getElementById('movieNotFoundPrompt'),
    movieNotFoundButton: document.getElementById('movieNotFoundButton'),
    addMovieModal: document.getElementById('addRadarrModal'),
    addMovieBackdrop: document.getElementById('addRadarrBackdrop'),
    addMovieSearchInput: document.getElementById('addRadarrSearch'),
    addMovieSearchButton: document.getElementById('addRadarrSearchButton'),
    addMovieStatus: document.getElementById('addRadarrStatus'),
    addMovieResults: document.getElementById('addRadarrResults'),
    addMoviePreview: document.getElementById('addRadarrPreview'),
    addMoviePoster: document.getElementById('addRadarrPoster'),
    addMoviePreviewTitle: document.getElementById('addRadarrPreviewTitle'),
    addMoviePreviewMeta: document.getElementById('addRadarrPreviewMeta'),
    addMoviePreviewOverview: document.getElementById('addRadarrPreviewOverview'),
    addMovieConfirmButton: document.getElementById('addRadarrConfirm'),
    addMovieCloseButtons: document.querySelectorAll('[data-close-add-movie]'),
    toggleConsoleButton: document.getElementById('toggleConsoleButton'),
    sideColumn: document.getElementById('debugConsoleRegion')
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

  const CONSOLE_VISIBILITY_STORAGE_KEY = 'yt2radarr.consoleVisible';

  function readConsoleVisibilityPreference(defaultValue = true) {
    try {
      const stored = window.localStorage.getItem(CONSOLE_VISIBILITY_STORAGE_KEY);
      if (stored === 'true' || stored === 'false') {
        return stored === 'true';
      }
    } catch (err) {
      // Local storage may be unavailable (e.g., in private browsing mode)
    }
    return defaultValue;
  }

  function persistConsoleVisibility(value) {
    try {
      window.localStorage.setItem(
        CONSOLE_VISIBILITY_STORAGE_KEY,
        value ? 'true' : 'false'
      );
    } catch (err) {
      // Ignore persistence errors
    }
  }

  const initialConsoleVisible = readConsoleVisibilityPreference(true);

  const state = {
    downloads: [],
    pollers: new Map(),
    debugMode: initialDebugMode,
    consoleVisible: initialConsoleVisible,
    lastLogs: [],
    copyFeedbackTimeout: null,
    selectedJobId: null,
    activeConsoleJobId: null,
    youtubeSearch: {
      timeout: null,
      token: 0,
      loading: false,
      results: [],
      selectedIndex: -1,
      lastSelectedUrl: '',
      controller: null
    },
    addMovie: {
      modalOpen: false,
      searchTimeout: null,
      searchToken: 0,
      loading: false,
      results: [],
      selectedIndex: -1,
      selectedMovie: null,
      adding: false,
      lastFocusedElement: null,
      query: ''
    }
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
  const YT_SEARCH_BUTTON_DEFAULT_LABEL = elements.ytSearchButton
    ? (elements.ytSearchButton.textContent || 'Search').trim() || 'Search'
    : 'Search';
  const YT_SEARCH_MIN_QUERY_LENGTH = 3;
  const YT_SEARCH_DEBOUNCE = 400;
  const YT_SEARCH_RESULT_LIMIT = 6;

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

  function clearYouTubeSearchTimer() {
    if (state.youtubeSearch.timeout) {
      clearTimeout(state.youtubeSearch.timeout);
      state.youtubeSearch.timeout = null;
    }
  }

  function abortOngoingYouTubeSearch() {
    const { controller } = state.youtubeSearch;
    if (controller && typeof controller.abort === 'function') {
      try {
        controller.abort();
      } catch (err) {
        // Ignore abort errors
      }
    }
    state.youtubeSearch.controller = null;
  }

  function setYouTubeSearchLoading(loading) {
    const value = Boolean(loading);
    state.youtubeSearch.loading = value;
    if (elements.ytSearchButton) {
      elements.ytSearchButton.disabled = value;
      elements.ytSearchButton.textContent = value ? 'Searching…' : YT_SEARCH_BUTTON_DEFAULT_LABEL;
    }
  }

  function setYouTubeSearchFeedback(message, type = 'info') {
    if (!elements.ytSearchFeedback) {
      return;
    }
    const text = message ? String(message).trim() : '';
    elements.ytSearchFeedback.textContent = text;
    elements.ytSearchFeedback.classList.remove('is-success', 'is-error', 'is-info', 'is-warning');
    if (!text) {
      elements.ytSearchFeedback.setAttribute('hidden', 'hidden');
      return;
    }
    let className = 'is-info';
    if (type === 'success') {
      className = 'is-success';
    } else if (type === 'error') {
      className = 'is-error';
    } else if (type === 'warning') {
      className = 'is-warning';
    }
    elements.ytSearchFeedback.classList.add(className);
    elements.ytSearchFeedback.removeAttribute('hidden');
  }

  function truncateText(value, maxLength = 200) {
    const text = value ? String(value).trim() : '';
    if (!text) {
      return '';
    }
    if (text.length <= maxLength) {
      return text;
    }
    return `${text.slice(0, Math.max(0, maxLength - 1)).trim()}…`;
  }

  function formatViewCount(value) {
    if (value === null || value === undefined) {
      return '';
    }
    const numeric = Number(value);
    if (!Number.isFinite(numeric) || numeric < 0) {
      return '';
    }
    const rounded = Math.round(numeric);
    if (rounded >= 1000000) {
      const millions = rounded / 1000000;
      return `${millions.toFixed(millions >= 10 ? 0 : 1)}M views`;
    }
    if (rounded >= 1000) {
      const thousands = rounded / 1000;
      return `${thousands.toFixed(thousands >= 10 ? 0 : 1)}K views`;
    }
    return `${rounded.toLocaleString()} views`;
  }

  function buildYouTubeMeta(result) {
    if (!result || typeof result !== 'object') {
      return '';
    }
    const parts = [];
    if (result.channel) {
      parts.push(String(result.channel));
    }
    if (result.upload_date) {
      parts.push(String(result.upload_date));
    }
    const viewLabel = formatViewCount(result.view_count);
    if (viewLabel) {
      parts.push(viewLabel);
    }
    if (result.live) {
      parts.push('Live');
    }
    return parts.join(' • ');
  }

  function renderYouTubeSearchResults() {
    if (!elements.ytSearchResults) {
      return;
    }
    const container = elements.ytSearchResults;
    container.innerHTML = '';
    const results = Array.isArray(state.youtubeSearch.results)
      ? state.youtubeSearch.results
      : [];
    if (!results.length) {
      container.setAttribute('hidden', 'hidden');
      return;
    }
    results.forEach((result, index) => {
      const item = document.createElement('div');
      item.classList.add('yt-search-result');
      if (index === state.youtubeSearch.selectedIndex) {
        item.classList.add('is-selected');
      }

      const button = document.createElement('button');
      button.type = 'button';
      button.classList.add('yt-search-result-button');
      button.dataset.index = String(index);
      button.setAttribute('aria-label', `Use YouTube result: ${result && result.title ? result.title : 'Video'}`);

      const thumbnailWrapper = document.createElement('div');
      thumbnailWrapper.classList.add('yt-search-thumbnail');
      const thumbnailUrl = result && result.thumbnail ? result.thumbnail : '';
      if (thumbnailUrl) {
        const img = document.createElement('img');
        img.src = thumbnailUrl;
        img.loading = 'lazy';
        img.alt = result && result.title ? `${result.title} thumbnail` : 'Video thumbnail';
        thumbnailWrapper.appendChild(img);
      } else {
        thumbnailWrapper.classList.add('is-placeholder');
        const placeholder = document.createElement('span');
        placeholder.textContent = 'No preview available';
        thumbnailWrapper.appendChild(placeholder);
      }

      const durationLabel = document.createElement('span');
      durationLabel.classList.add('yt-search-duration');
      if (result && result.live) {
        durationLabel.textContent = 'LIVE';
        durationLabel.classList.add('is-live');
      } else if (result && result.duration_text) {
        durationLabel.textContent = result.duration_text;
      }
      if (durationLabel.textContent) {
        thumbnailWrapper.appendChild(durationLabel);
      }

      const info = document.createElement('div');
      info.classList.add('yt-search-result-info');

      const title = document.createElement('div');
      title.classList.add('yt-search-result-title');
      title.textContent = result && result.title ? result.title : 'Untitled video';
      info.appendChild(title);

      const metaText = buildYouTubeMeta(result);
      if (metaText) {
        const meta = document.createElement('div');
        meta.classList.add('yt-search-result-meta');
        meta.textContent = metaText;
        info.appendChild(meta);
      }

      const description = truncateText(result && result.description ? result.description : '', 220);
      if (description) {
        const descriptionElem = document.createElement('div');
        descriptionElem.classList.add('yt-search-result-description');
        descriptionElem.textContent = description;
        info.appendChild(descriptionElem);
      }

      button.appendChild(thumbnailWrapper);
      button.appendChild(info);
      item.appendChild(button);
      container.appendChild(item);
    });
    container.removeAttribute('hidden');
  }

  function resetYouTubeSearchSelection() {
    state.youtubeSearch.selectedIndex = -1;
    state.youtubeSearch.lastSelectedUrl = '';
  }

  function selectYouTubeSearchResult(index) {
    const results = Array.isArray(state.youtubeSearch.results)
      ? state.youtubeSearch.results
      : [];
    const numericIndex = Number(index);
    if (!Number.isInteger(numericIndex) || numericIndex < 0 || numericIndex >= results.length) {
      return;
    }
    const result = results[numericIndex];
    state.youtubeSearch.selectedIndex = numericIndex;
    state.youtubeSearch.lastSelectedUrl = result && result.url ? result.url : '';
    if (elements.ytInput && result && result.url) {
      elements.ytInput.value = result.url;
      elements.ytInput.focus();
      elements.ytInput.dispatchEvent(new Event('input', { bubbles: true }));
      elements.ytInput.select();
    }
    renderYouTubeSearchResults();
    if (result && result.title) {
      setYouTubeSearchFeedback(`Selected “${result.title}”. URL filled below.`, 'success');
    } else {
      setYouTubeSearchFeedback('Selected video. URL filled below.', 'success');
    }
  }

  function scheduleYouTubeSearch(options = {}) {
    if (!elements.ytSearchInput) {
      return;
    }
    const query = elements.ytSearchInput.value ? elements.ytSearchInput.value.trim() : '';
    clearYouTubeSearchTimer();
    if (!query) {
      abortOngoingYouTubeSearch();
      state.youtubeSearch.results = [];
      resetYouTubeSearchSelection();
      renderYouTubeSearchResults();
      setYouTubeSearchLoading(false);
      setYouTubeSearchFeedback('', 'info');
      return;
    }
    if (query.length < YT_SEARCH_MIN_QUERY_LENGTH) {
      abortOngoingYouTubeSearch();
      state.youtubeSearch.results = [];
      resetYouTubeSearchSelection();
      renderYouTubeSearchResults();
      setYouTubeSearchLoading(false);
      setYouTubeSearchFeedback(`Enter at least ${YT_SEARCH_MIN_QUERY_LENGTH} characters to search YouTube.`, 'info');
      return;
    }
    if (options.immediate) {
      performYouTubeSearch(query);
      return;
    }
    state.youtubeSearch.timeout = setTimeout(() => {
      performYouTubeSearch(query);
    }, YT_SEARCH_DEBOUNCE);
  }

  async function performYouTubeSearch(query) {
    clearYouTubeSearchTimer();
    const trimmedQuery = query ? String(query).trim() : '';
    if (!trimmedQuery) {
      return;
    }
    abortOngoingYouTubeSearch();
    const token = ++state.youtubeSearch.token;
    setYouTubeSearchLoading(true);
    resetYouTubeSearchSelection();
    setYouTubeSearchFeedback('Searching YouTube…', 'info');
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    state.youtubeSearch.controller = controller;
    try {
      const params = new URLSearchParams();
      params.set('query', trimmedQuery);
      if (YT_SEARCH_RESULT_LIMIT) {
        params.set('limit', String(YT_SEARCH_RESULT_LIMIT));
      }
      const response = await fetch(`/youtube/search?${params.toString()}`, {
        signal: controller ? controller.signal : undefined
      });
      const data = await response.json().catch(() => ({}));
      if (token !== state.youtubeSearch.token) {
        return;
      }
      if (!response.ok) {
        state.youtubeSearch.results = [];
        renderYouTubeSearchResults();
        const message = data && data.error ? data.error : `Search failed (HTTP ${response.status}).`;
        setYouTubeSearchFeedback(message, 'error');
        return;
      }
      const results = Array.isArray(data.results) ? data.results : [];
      state.youtubeSearch.results = results;
      resetYouTubeSearchSelection();
      renderYouTubeSearchResults();
      if (!results.length) {
        setYouTubeSearchFeedback('No videos found for that search.', 'warning');
      } else {
        const prefix = data && data.cached ? 'Showing cached' : 'Showing';
        setYouTubeSearchFeedback(
          `${prefix} ${results.length} result${results.length === 1 ? '' : 's'}. Select one to fill the URL field.`,
          'info'
        );
      }
    } catch (err) {
      if (controller && err && err.name === 'AbortError') {
        return;
      }
      if (token !== state.youtubeSearch.token) {
        return;
      }
      state.youtubeSearch.results = [];
      renderYouTubeSearchResults();
      setYouTubeSearchFeedback(
        `Failed to search YouTube: ${err && err.message ? err.message : err}`,
        'error'
      );
    } finally {
      if (token === state.youtubeSearch.token) {
        state.youtubeSearch.controller = null;
        setYouTubeSearchLoading(false);
      } else if (state.youtubeSearch.controller === controller) {
        state.youtubeSearch.controller = null;
      }
    }
  }

  function handleYouTubeUrlInputChange() {
    if (!elements.ytInput) {
      return;
    }
    const value = elements.ytInput.value ? elements.ytInput.value.trim() : '';
    if (!value || value !== state.youtubeSearch.lastSelectedUrl) {
      if (state.youtubeSearch.selectedIndex !== -1) {
        state.youtubeSearch.selectedIndex = -1;
        renderYouTubeSearchResults();
      }
      state.youtubeSearch.lastSelectedUrl = value;
    }
  }

  function setConsoleVisibility(enabled, options = {}) {
    const { skipStorage = false } = options || {};
    const value = Boolean(enabled);
    state.consoleVisible = value;
    if (document.body && document.body.dataset) {
      document.body.dataset.consoleVisible = value ? 'true' : 'false';
    }
    if (elements.toggleConsoleButton) {
      elements.toggleConsoleButton.textContent = value ? 'Hide Console' : 'Show Console';
      elements.toggleConsoleButton.setAttribute('aria-expanded', value ? 'true' : 'false');
      elements.toggleConsoleButton.setAttribute('aria-pressed', value ? 'true' : 'false');
    }
    if (elements.sideColumn) {
      if (value) {
        elements.sideColumn.removeAttribute('aria-hidden');
      } else {
        elements.sideColumn.setAttribute('aria-hidden', 'true');
      }
    }
    if (!skipStorage) {
      persistConsoleVisibility(value);
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

  function getMovieOptions() {
    if (!elements.movieOptions) {
      return [];
    }
    return Array.from(elements.movieOptions.querySelectorAll('option'));
  }

  function findMatchingMovieOption(value) {
    if (!value) {
      return null;
    }
    const target = value.trim();
    if (!target) {
      return null;
    }
    return getMovieOptions().find(option => (option.value || '').trim() === target) || null;
  }

  function buildMovieOptionValue(movie) {
    const title = (movie && movie.title ? String(movie.title) : '').trim() || 'Movie';
    const year = movie && movie.year ? String(movie.year).trim() : '';
    return year ? `${title} (${year})` : title;
  }

  function upsertMovieOption(movie) {
    if (!elements.movieOptions || !movie || typeof movie !== 'object') {
      return null;
    }
    const movieId = movie.id != null ? String(movie.id) : '';
    if (!movieId) {
      return null;
    }
    const tmdbId = movie.tmdbId != null ? String(movie.tmdbId) : '';
    const title = (movie.title || '').trim();
    const year = movie.year != null ? String(movie.year).trim() : '';
    const label = buildMovieOptionValue({ title, year });
    const options = getMovieOptions();
    let option = options.find(opt => opt.getAttribute('data-id') === movieId);
    if (!option) {
      option = document.createElement('option');
      elements.movieOptions.appendChild(option);
    }
    option.value = label;
    option.setAttribute('data-id', movieId);
    option.setAttribute('data-title', title);
    option.setAttribute('data-year', year);
    option.setAttribute('data-tmdb', tmdbId);
    return option;
  }

  function setMovieFeedback(message, type = 'info') {
    if (!elements.movieFeedback) {
      return;
    }
    const text = message ? String(message).trim() : '';
    elements.movieFeedback.textContent = text;
    elements.movieFeedback.classList.remove('is-success', 'is-error', 'is-info', 'is-warning');
    if (!text) {
      elements.movieFeedback.setAttribute('hidden', 'hidden');
      return;
    }
    let className = 'is-info';
    if (type === 'success') {
      className = 'is-success';
    } else if (type === 'error') {
      className = 'is-error';
    } else if (type === 'warning') {
      className = 'is-warning';
    }
    elements.movieFeedback.classList.add(className);
    elements.movieFeedback.removeAttribute('hidden');
  }

  function clearMovieFeedback() {
    setMovieFeedback('');
  }

  function updateMovieNotFoundPrompt() {
    if (!elements.movieNotFoundPrompt || !elements.movieNameInput) {
      return;
    }
    const value = elements.movieNameInput.value ? elements.movieNameInput.value.trim() : '';
    const hasValue = Boolean(value);
    const option = hasValue ? findMatchingMovieOption(value) : null;
    if (!hasValue || option) {
      elements.movieNotFoundPrompt.setAttribute('hidden', 'hidden');
    } else {
      elements.movieNotFoundPrompt.removeAttribute('hidden');
    }
  }

  function clearMovieSelection() {
    if (elements.movieIdInput) elements.movieIdInput.value = '';
    if (elements.titleInput) elements.titleInput.value = '';
    if (elements.yearInput) elements.yearInput.value = '';
    if (elements.tmdbInput) elements.tmdbInput.value = '';
  }

  function applyMovieOption(option) {
    if (!option) {
      clearMovieSelection();
      return;
    }
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
  }

  function syncMovieSelection() {
    if (!elements.movieNameInput) {
      return;
    }
    const value = elements.movieNameInput.value ? elements.movieNameInput.value.trim() : '';
    const option = findMatchingMovieOption(value);
    if (option) {
      applyMovieOption(option);
    } else {
      clearMovieSelection();
    }
    updateMovieNotFoundPrompt();
  }

  function clearAddMovieSearchTimer() {
    if (state.addMovie.searchTimeout) {
      clearTimeout(state.addMovie.searchTimeout);
      state.addMovie.searchTimeout = null;
    }
  }

  function setAddMovieStatus(message, type = 'info') {
    if (!elements.addMovieStatus) {
      return;
    }
    const text = message ? String(message).trim() : '';
    elements.addMovieStatus.textContent = text;
    elements.addMovieStatus.classList.remove('is-success', 'is-error', 'is-info', 'is-warning');
    if (!text) {
      return;
    }
    let className = 'is-info';
    if (type === 'success') {
      className = 'is-success';
    } else if (type === 'error') {
      className = 'is-error';
    } else if (type === 'warning') {
      className = 'is-warning';
    }
    elements.addMovieStatus.classList.add(className);
  }

  function clearAddMoviePreview() {
    state.addMovie.selectedMovie = null;
    if (elements.addMoviePreview) {
      elements.addMoviePreview.setAttribute('hidden', 'hidden');
    }
    if (elements.addMoviePoster) {
      elements.addMoviePoster.removeAttribute('src');
      elements.addMoviePoster.setAttribute('hidden', 'hidden');
    }
    if (elements.addMoviePreviewTitle) {
      elements.addMoviePreviewTitle.textContent = 'Select a movie';
    }
    if (elements.addMoviePreviewMeta) {
      elements.addMoviePreviewMeta.textContent = '';
    }
    if (elements.addMoviePreviewOverview) {
      elements.addMoviePreviewOverview.textContent = '';
    }
    if (elements.addMovieConfirmButton) {
      elements.addMovieConfirmButton.disabled = true;
    }
  }

  function findPosterUrl(movie) {
    if (!movie) {
      return '';
    }
    if (movie.remotePoster) {
      return String(movie.remotePoster);
    }
    const images = Array.isArray(movie.images) ? movie.images : [];
    const poster = images.find(image => {
      if (!image || typeof image !== 'object') {
        return false;
      }
      const coverType = (image.coverType || '').toLowerCase();
      return coverType === 'poster' && (image.remoteUrl || image.url);
    }) || images.find(image => image && (image.remoteUrl || image.url));
    if (!poster) {
      return '';
    }
    return poster.remoteUrl || poster.url || '';
  }

  function renderAddMoviePreview(movie) {
    if (!movie) {
      clearAddMoviePreview();
      return;
    }
    state.addMovie.selectedMovie = movie;
    if (elements.addMoviePreview) {
      elements.addMoviePreview.removeAttribute('hidden');
    }
    const title = (movie.title || '').trim() || 'Movie';
    const year = movie.year ? String(movie.year).trim() : '';
    if (elements.addMoviePreviewTitle) {
      elements.addMoviePreviewTitle.textContent = year ? `${title} (${year})` : title;
    }
    const metaParts = [];
    if (movie.runtime) {
      metaParts.push(`${movie.runtime} min`);
    }
    if (Array.isArray(movie.genres) && movie.genres.length) {
      metaParts.push(movie.genres.slice(0, 3).join(', '));
    }
    if (elements.addMoviePreviewMeta) {
      elements.addMoviePreviewMeta.textContent = metaParts.join(' • ');
    }
    if (elements.addMoviePreviewOverview) {
      const overview = (movie.overview || '').trim();
      elements.addMoviePreviewOverview.textContent = overview || 'No overview available for this title.';
    }
    if (elements.addMoviePoster) {
      const posterUrl = findPosterUrl(movie);
      if (posterUrl) {
        elements.addMoviePoster.src = posterUrl;
        elements.addMoviePoster.removeAttribute('hidden');
      } else {
        elements.addMoviePoster.removeAttribute('src');
        elements.addMoviePoster.setAttribute('hidden', 'hidden');
      }
    }
    if (elements.addMovieConfirmButton) {
      elements.addMovieConfirmButton.disabled = false;
    }
  }

  function renderAddMovieResults() {
    if (!elements.addMovieResults) {
      return;
    }
    const results = Array.isArray(state.addMovie.results) ? state.addMovie.results : [];
    elements.addMovieResults.innerHTML = '';
    if (!results.length) {
      elements.addMovieResults.setAttribute('hidden', 'hidden');
      return;
    }
    elements.addMovieResults.removeAttribute('hidden');
    results.forEach((movie, index) => {
      const item = document.createElement('li');
      item.className = 'modal-result';
      if (index === state.addMovie.selectedIndex) {
        item.classList.add('is-selected');
      }
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'modal-result-button';
      button.dataset.index = String(index);

      const titleSpan = document.createElement('span');
      titleSpan.className = 'result-title';
      const title = (movie.title || '').trim() || 'Movie';
      const year = movie.year ? String(movie.year).trim() : '';
      titleSpan.textContent = year ? `${title} (${year})` : title;
      button.appendChild(titleSpan);

      const metaParts = [];
      if (movie.runtime) {
        metaParts.push(`${movie.runtime} min`);
      }
      if (Array.isArray(movie.genres) && movie.genres.length) {
        metaParts.push(movie.genres.slice(0, 2).join(', '));
      }
      if (metaParts.length) {
        const meta = document.createElement('span');
        meta.className = 'result-meta';
        meta.textContent = metaParts.join(' • ');
        button.appendChild(meta);
      }

      if (movie.overview) {
        const overview = document.createElement('span');
        overview.className = 'result-overview';
        const summary = String(movie.overview).trim();
        overview.textContent = summary.length > 180 ? `${summary.slice(0, 177)}…` : summary;
        button.appendChild(overview);
      }

      item.appendChild(button);
      elements.addMovieResults.appendChild(item);
    });
  }

  function resetAddMovieState() {
    clearAddMovieSearchTimer();
    state.addMovie.loading = false;
    state.addMovie.results = [];
    state.addMovie.selectedIndex = -1;
    state.addMovie.selectedMovie = null;
    state.addMovie.adding = false;
    state.addMovie.query = '';
    setAddMovieStatus('');
    renderAddMovieResults();
    clearAddMoviePreview();
  }

  function openAddMovieModal(initialQuery = '') {
    if (!elements.addMovieModal) {
      return;
    }
    clearMovieFeedback();
    state.addMovie.modalOpen = true;
    state.addMovie.lastFocusedElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    state.addMovie.searchToken = 0;
    elements.addMovieModal.removeAttribute('hidden');
    document.body.classList.add('modal-open');
    resetAddMovieState();
    const query = (initialQuery || '').trim();
    if (elements.addMovieSearchInput) {
      elements.addMovieSearchInput.value = query;
      elements.addMovieSearchInput.focus();
      if (query.length >= 2) {
        scheduleAddMovieSearch({ immediate: true });
      } else {
        setAddMovieStatus('Enter at least 2 characters to search Radarr.', 'info');
      }
    } else {
      setAddMovieStatus('Enter at least 2 characters to search Radarr.', 'info');
    }
  }

  function closeAddMovieModal(options = {}) {
    if (!elements.addMovieModal || !state.addMovie.modalOpen) {
      return;
    }
    elements.addMovieModal.setAttribute('hidden', 'hidden');
    document.body.classList.remove('modal-open');
    resetAddMovieState();
    state.addMovie.modalOpen = false;
    const restoreFocus = options.restoreFocus !== false;
    const lastFocused = state.addMovie.lastFocusedElement;
    state.addMovie.lastFocusedElement = null;
    if (restoreFocus && lastFocused && typeof lastFocused.focus === 'function') {
      lastFocused.focus();
    }
  }

  function selectAddMovieResult(index) {
    const results = Array.isArray(state.addMovie.results) ? state.addMovie.results : [];
    const numericIndex = Number(index);
    if (!Number.isInteger(numericIndex) || numericIndex < 0 || numericIndex >= results.length) {
      return;
    }
    state.addMovie.selectedIndex = numericIndex;
    renderAddMovieResults();
    renderAddMoviePreview(results[numericIndex]);
    setAddMovieStatus('Ready to add this movie to Radarr.', 'success');
  }

  function scheduleAddMovieSearch(options = {}) {
    if (!elements.addMovieSearchInput) {
      return;
    }
    const query = elements.addMovieSearchInput.value ? elements.addMovieSearchInput.value.trim() : '';
    state.addMovie.query = query;
    clearAddMovieSearchTimer();
    if (!query || query.length < 2) {
      resetAddMovieState();
      setAddMovieStatus('Enter at least 2 characters to search Radarr.', 'info');
      return;
    }
    if (options.immediate) {
      performAddMovieSearch(query);
      return;
    }
    state.addMovie.searchTimeout = setTimeout(() => {
      performAddMovieSearch(query);
    }, 350);
  }

  async function performAddMovieSearch(query) {
    const token = ++state.addMovie.searchToken;
    state.addMovie.loading = true;
    setAddMovieStatus('Searching Radarr…', 'info');
    if (elements.addMovieConfirmButton) {
      elements.addMovieConfirmButton.disabled = true;
    }
    try {
      const response = await fetch(`/radarr/search?query=${encodeURIComponent(query)}`);
      const data = await response.json().catch(() => ({}));
      if (token !== state.addMovie.searchToken) {
        return;
      }
      if (!response.ok) {
        const message = data && data.error ? data.error : `Search failed (HTTP ${response.status}).`;
        state.addMovie.results = [];
        state.addMovie.selectedIndex = -1;
        state.addMovie.selectedMovie = null;
        renderAddMovieResults();
        clearAddMoviePreview();
        setAddMovieStatus(message, 'error');
        return;
      }
      const results = Array.isArray(data.results) ? data.results.filter(item => item && item.tmdbId) : [];
      state.addMovie.results = results;
      state.addMovie.selectedIndex = -1;
      state.addMovie.selectedMovie = null;
      renderAddMovieResults();
      clearAddMoviePreview();
      if (!results.length) {
        setAddMovieStatus('No matches found in Radarr for that search.', 'warning');
      } else {
        setAddMovieStatus('Select a movie below to add it to Radarr.', 'info');
      }
    } catch (err) {
      if (token !== state.addMovie.searchToken) {
        return;
      }
      state.addMovie.results = [];
      state.addMovie.selectedIndex = -1;
      state.addMovie.selectedMovie = null;
      renderAddMovieResults();
      clearAddMoviePreview();
      setAddMovieStatus(`Failed to search Radarr: ${err && err.message ? err.message : err}`, 'error');
    } finally {
      if (token === state.addMovie.searchToken) {
        state.addMovie.loading = false;
      }
    }
  }

  function handleMovieAdded(movie) {
    upsertMovieOption(movie);
    if (elements.movieNameInput) {
      elements.movieNameInput.value = buildMovieOptionValue(movie);
      elements.movieNameInput.focus();
      elements.movieNameInput.select();
    }
    syncMovieSelection();
    updateMovieNotFoundPrompt();
    if (movie && movie.title) {
      appendConsoleLine(`Added movie to Radarr: ${buildMovieOptionValue(movie)}`);
    }
  }

  async function handleAddMovieConfirm() {
    if (!elements.addMovieConfirmButton || elements.addMovieConfirmButton.disabled) {
      return;
    }
    const movie = state.addMovie.selectedMovie;
    if (!movie || !movie.tmdbId) {
      return;
    }
    const originalLabel = elements.addMovieConfirmButton.textContent;
    elements.addMovieConfirmButton.disabled = true;
    elements.addMovieConfirmButton.textContent = 'Adding…';
    state.addMovie.adding = true;
    setAddMovieStatus('Adding movie to Radarr…', 'info');
    try {
      const response = await fetch('/radarr/movies', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tmdbId: movie.tmdbId, search: true })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const message = data && data.error ? data.error : `Failed to add movie (HTTP ${response.status}).`;
        setAddMovieStatus(message, 'error');
        elements.addMovieConfirmButton.disabled = false;
        elements.addMovieConfirmButton.textContent = originalLabel;
        state.addMovie.adding = false;
        return;
      }
      const created = data && data.movie ? data.movie : null;
      if (!created) {
        setAddMovieStatus('Movie was added, but no details were returned.', 'warning');
        elements.addMovieConfirmButton.disabled = false;
        elements.addMovieConfirmButton.textContent = originalLabel;
        state.addMovie.adding = false;
        return;
      }
      handleMovieAdded(created);
      closeAddMovieModal();
      setMovieFeedback(`Added "${buildMovieOptionValue(created)}" to Radarr and selected it.`, 'success');
    } catch (err) {
      const message = err && err.message ? err.message : err;
      setAddMovieStatus(`Failed to add movie to Radarr: ${message}`, 'error');
      elements.addMovieConfirmButton.disabled = false;
    } finally {
      state.addMovie.adding = false;
      if (elements.addMovieConfirmButton) {
        elements.addMovieConfirmButton.textContent = originalLabel;
      }
    }
  }

  function handleMovieNameInput() {
    syncMovieSelection();
    clearMovieFeedback();
  }

  function initialiseMovieNotFoundPrompt() {
    updateMovieNotFoundPrompt();
    if (elements.movieNotFoundButton) {
      elements.movieNotFoundButton.addEventListener('click', () => {
        const initialQuery = elements.movieNameInput ? elements.movieNameInput.value.trim() : '';
        openAddMovieModal(initialQuery);
      });
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

  function buildDownloadItem(entry, options = {}) {
    const { isSelected = false } = options;
    const wrapper = document.createElement('div');
    wrapper.className = 'download-item';
    wrapper.dataset.status = entry.status;
    if (entry.id) {
      wrapper.dataset.jobId = entry.id;
    }
    wrapper.classList.add('is-interactive');
    wrapper.setAttribute('tabindex', '0');
    wrapper.setAttribute('role', 'button');
    const labelText = entry.label ? `View logs for ${entry.label}` : 'View logs for job';
    wrapper.setAttribute('aria-label', labelText);
    if (isSelected) {
      wrapper.classList.add('is-selected');
      wrapper.setAttribute('aria-current', 'true');
    }

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
    if (
      state.selectedJobId &&
      !state.downloads.some(item => item && item.id === state.selectedJobId)
    ) {
      if (state.activeConsoleJobId === state.selectedJobId) {
        state.activeConsoleJobId = null;
      }
      state.selectedJobId = null;
    }
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
      const isSelected = state.selectedJobId === entry.id;
      elements.downloadsList.appendChild(buildDownloadItem(entry, { isSelected }));
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
    const shouldNotifyNotFound = Boolean(options.notifyNotFound);
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
        if (
          (options.showConsole || state.activeConsoleJobId === jobId) &&
          shouldNotifyNotFound
        ) {
          renderLogLines(['ERROR: Job not found or has expired.']);
        }
        if (state.activeConsoleJobId === jobId) {
          state.activeConsoleJobId = null;
        }
        if (state.selectedJobId === jobId) {
          state.selectedJobId = null;
          renderDownloads();
        }
        stopJobPolling(jobId);
        return;
      }
      const entry = normaliseJob(job);
      if (entry) {
        upsertDownload(entry);
      }
      const showConsole = Boolean(options.showConsole) || state.activeConsoleJobId === jobId;
      if (showConsole && Array.isArray(job.logs)) {
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
    if (!elements.movieNameInput) {
      return;
    }
    const value = elements.movieNameInput.value ? elements.movieNameInput.value.trim() : '';
    const option = findMatchingMovieOption(value);
    if (option) {
      applyMovieOption(option);
    } else {
      clearMovieSelection();
    }
    updateMovieNotFoundPrompt();
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

  if (elements.ytSearchInput) {
    elements.ytSearchInput.addEventListener('input', () => scheduleYouTubeSearch());
    elements.ytSearchInput.addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault();
        scheduleYouTubeSearch({ immediate: true });
      }
    });
  }

  if (elements.ytSearchButton) {
    elements.ytSearchButton.addEventListener('click', () => {
      scheduleYouTubeSearch({ immediate: true });
    });
  }

  if (elements.ytSearchResults) {
    elements.ytSearchResults.addEventListener('click', event => {
      const button = event.target instanceof Element ? event.target.closest('.yt-search-result-button') : null;
      if (!button) {
        return;
      }
      const indexValue = button.dataset ? button.dataset.index : null;
      const index = indexValue !== null && indexValue !== undefined ? Number(indexValue) : NaN;
      if (!Number.isInteger(index)) {
        return;
      }
      event.preventDefault();
      selectYouTubeSearchResult(index);
    });

    elements.ytSearchResults.addEventListener('keydown', event => {
      if (event.defaultPrevented) {
        return;
      }
      if (event.key !== 'Enter' && event.key !== ' ') {
        return;
      }
      const button = event.target instanceof Element ? event.target.closest('.yt-search-result-button') : null;
      if (!button) {
        return;
      }
      const indexValue = button.dataset ? button.dataset.index : null;
      const index = indexValue !== null && indexValue !== undefined ? Number(indexValue) : NaN;
      if (!Number.isInteger(index)) {
        return;
      }
      event.preventDefault();
      selectYouTubeSearchResult(index);
    });
  }

  if (elements.ytInput) {
    elements.ytInput.addEventListener('input', handleYouTubeUrlInputChange);
  }

  if (elements.movieNameInput) {
    elements.movieNameInput.addEventListener('input', handleMovieNameInput);
    elements.movieNameInput.addEventListener('change', handleMovieNameInput);
  }

  initialiseMovieNotFoundPrompt();
  syncMovieSelection();

  if (elements.addMovieBackdrop) {
    elements.addMovieBackdrop.addEventListener('click', () => {
      closeAddMovieModal();
    });
  }

  if (elements.addMovieCloseButtons && typeof elements.addMovieCloseButtons.forEach === 'function') {
    elements.addMovieCloseButtons.forEach(button => {
      button.addEventListener('click', () => {
        closeAddMovieModal();
      });
    });
  }

  if (elements.addMovieSearchInput) {
    elements.addMovieSearchInput.addEventListener('input', () => scheduleAddMovieSearch());
    elements.addMovieSearchInput.addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault();
        scheduleAddMovieSearch({ immediate: true });
      }
    });
  }

  if (elements.addMovieSearchButton) {
    elements.addMovieSearchButton.addEventListener('click', () => {
      scheduleAddMovieSearch({ immediate: true });
    });
  }

  if (elements.addMovieResults) {
    elements.addMovieResults.addEventListener('click', event => {
      const button = event.target instanceof Element ? event.target.closest('.modal-result-button') : null;
      if (!button) {
        return;
      }
      const index = button.dataset ? button.dataset.index : null;
      if (index === null || index === undefined) {
        return;
      }
      event.preventDefault();
      selectAddMovieResult(Number(index));
    });
  }

  if (elements.addMovieConfirmButton) {
    elements.addMovieConfirmButton.addEventListener('click', handleAddMovieConfirm);
  }

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && state.addMovie.modalOpen) {
      event.preventDefault();
      closeAddMovieModal();
    }
  });

  if (elements.extraCheckbox) {
    elements.extraCheckbox.addEventListener('change', updateExtraVisibility);
  }

  if (elements.copyButton) {
    elements.copyButton.addEventListener('click', copyFullLogToClipboard);
  }

  function findDownloadItem(target) {
    if (!(target instanceof Element)) {
      return null;
    }
    return target.closest('.download-item');
  }

  function activateDownloadItem(jobId) {
    if (!jobId) {
      return;
    }
    const previousActive = state.activeConsoleJobId;
    state.selectedJobId = jobId;
    state.activeConsoleJobId = jobId;
    renderDownloads();
    const entry = state.downloads.find(item => item && item.id === jobId);
    const label = entry && entry.label ? entry.label : 'job';
    if (previousActive !== jobId) {
      resetConsole(`Loading logs for ${label}...`);
    }
    pollJob(jobId, { showConsole: true, notifyNotFound: true });
  }

  if (elements.downloadsList) {
    elements.downloadsList.addEventListener('click', event => {
      const item = findDownloadItem(event.target);
      if (!item || !item.classList.contains('is-interactive')) {
        return;
      }
      const jobId = item.dataset ? item.dataset.jobId : null;
      if (!jobId) {
        return;
      }
      event.preventDefault();
      activateDownloadItem(jobId);
    });

    elements.downloadsList.addEventListener('keydown', event => {
      if (event.defaultPrevented) {
        return;
      }
      if (event.key !== 'Enter' && event.key !== ' ') {
        return;
      }
      const item = findDownloadItem(event.target);
      if (!item || !item.classList.contains('is-interactive')) {
        return;
      }
      const jobId = item.dataset ? item.dataset.jobId : null;
      if (!jobId) {
        return;
      }
      event.preventDefault();
      activateDownloadItem(jobId);
    });
  }

  if (elements.toggleConsoleButton) {
    elements.toggleConsoleButton.addEventListener('click', () => {
      setConsoleVisibility(!state.consoleVisible);
    });
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
      playlist_mode: elements.playlistModeSelect ? elements.playlistModeSelect.value : 'single'
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

  renderYouTubeSearchResults();
  setYouTubeSearchLoading(false);
  if (elements.ytSearchFeedback && elements.ytSearchInput) {
    setYouTubeSearchFeedback(
      `Enter at least ${YT_SEARCH_MIN_QUERY_LENGTH} characters to search YouTube.`,
      'info'
    );
  }

  setConsoleVisibility(initialConsoleVisible, { skipStorage: true });
  setDebugMode(initialDebugMode);
  updateExtraVisibility();
  renderDownloads();
  loadInitialJobs();
});
