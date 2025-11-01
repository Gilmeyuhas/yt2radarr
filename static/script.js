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
    standaloneCheckbox: document.getElementById('standalone'),
    standaloneNamingOptions: document.getElementById('standaloneNamingOptions'),
    standaloneCustomToggle: document.getElementById('standaloneCustomNameToggle'),
    standaloneCustomGroup: document.getElementById('standaloneCustomNameGroup'),
    standaloneCustomInput: document.getElementById('standaloneCustomName'),
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
    sideColumn: document.getElementById('debugConsoleRegion'),
    refreshLibraryButton: document.getElementById('refreshLibraryButton'),
    themeToggleButton: document.getElementById('themeToggleButton'),
    youtubeSearchButton: document.getElementById('youtubeSearchButton'),
    youtubeSearchModal: document.getElementById('youtubeSearchModal'),
    youtubeSearchBackdrop: document.getElementById('youtubeSearchBackdrop'),
    youtubeSearchCloseButtons: document.querySelectorAll('[data-close-youtube-search]'),
    youtubeSearchInput: document.getElementById('youtubeSearchInput'),
    youtubeSearchSubmit: document.getElementById('youtubeSearchSubmit'),
    youtubeSearchStatus: document.getElementById('youtubeSearchStatus'),
    youtubeSearchResults: document.getElementById('youtubeSearchResults')
  };

  const THEME_STORAGE_KEY = 'yt2radarr.theme';
  const systemThemeQuery =
    typeof window.matchMedia === 'function' ? window.matchMedia('(prefers-color-scheme: light)') : null;
  const storedTheme = readStoredThemePreference();
  let userSelectedTheme = storedTheme;
  let activeTheme = storedTheme || (systemThemeQuery && systemThemeQuery.matches ? 'light' : 'dark');

  applyTheme(activeTheme, { persist: false });

  if (elements.themeToggleButton) {
    elements.themeToggleButton.addEventListener('click', () => {
      const nextTheme = activeTheme === 'light' ? 'dark' : 'light';
      applyTheme(nextTheme);
    });
  }

  if (systemThemeQuery) {
    const handleSystemThemeChange = (event) => {
      if (userSelectedTheme) {
        return;
      }
      applyTheme(event.matches ? 'light' : 'dark', { persist: false });
    };
    if (typeof systemThemeQuery.addEventListener === 'function') {
      systemThemeQuery.addEventListener('change', handleSystemThemeChange);
    } else if (typeof systemThemeQuery.addListener === 'function') {
      systemThemeQuery.addListener(handleSystemThemeChange);
    }
  }

  function readStoredThemePreference() {
    try {
      const storedValue = window.localStorage.getItem(THEME_STORAGE_KEY);
      if (storedValue === 'light' || storedValue === 'dark') {
        return storedValue;
      }
    } catch (err) {
      // Ignore storage errors
    }
    return null;
  }

  function persistThemePreference(value) {
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, value);
    } catch (err) {
      // Ignore persistence errors
    }
  }

  function updateThemeToggleButton(theme) {
    if (!elements.themeToggleButton) {
      return;
    }
    const isLight = theme === 'light';
    elements.themeToggleButton.textContent = isLight ? '🌙 Dark Mode' : '🌞 Light Mode';
    elements.themeToggleButton.setAttribute('aria-pressed', isLight ? 'true' : 'false');
    elements.themeToggleButton.setAttribute(
      'title',
      isLight ? 'Switch to dark mode' : 'Switch to light mode'
    );
  }

  function applyTheme(theme, options = {}) {
    const { persist = true } = options || {};
    const normalized = theme === 'light' ? 'light' : 'dark';
    activeTheme = normalized;
    if (document.body && document.body.dataset) {
      document.body.dataset.theme = normalized;
    }
    updateThemeToggleButton(normalized);
    if (persist) {
      userSelectedTheme = normalized;
      persistThemePreference(normalized);
    }
  }

  if (!elements.form) {
    return;
  }

  const STATUS_LABELS = {
    queued: 'Queued',
    processing: 'Processing',
    complete: 'Completed',
    failed: 'Failed',
    cancelled: 'Cancelled'
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

  const CANCELLABLE_STATUSES = new Set(['queued', 'processing']);
  const YOUTUBE_URL_PATTERN = /^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.be)\//i;

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
    pendingCancellations: new Set(),
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
    },
    youtubeSearch: {
      modalOpen: false,
      searchTimeout: null,
      searchToken: 0,
      loading: false,
      results: [],
      selectedIndex: -1,
      lastFocusedElement: null,
      query: ''
    }
  };

  function refreshModalOpenState() {
    if (!document.body) {
      return;
    }
    if (state.addMovie.modalOpen || state.youtubeSearch.modalOpen) {
      document.body.classList.add('modal-open');
    } else {
      document.body.classList.remove('modal-open');
    }
  }

  const IMPORTANT_LINE_SNIPPETS = [
    'success! video saved',
    'renaming downloaded file',
    'treating video as main video file',
    'storing video in subfolder',
    'created movie folder',
    'created standalone folder',
    'standalone folder resolved',
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

  function replaceMovieOptions(movies) {
    if (!elements.movieOptions) {
      return;
    }
    elements.movieOptions.innerHTML = '';
    if (!Array.isArray(movies) || !movies.length) {
      return;
    }
    const fragment = document.createDocumentFragment();
    movies.forEach(movie => {
      if (!movie || typeof movie !== 'object') {
        return;
      }
      const movieId = movie.id != null ? String(movie.id) : '';
      if (!movieId) {
        return;
      }
      const tmdbId = movie.tmdbId != null ? String(movie.tmdbId) : '';
      const title = (movie.title || '').trim();
      const year = movie.year != null ? String(movie.year).trim() : '';
      const option = document.createElement('option');
      option.value = buildMovieOptionValue({ title, year });
      option.setAttribute('data-id', movieId);
      option.setAttribute('data-title', title);
      option.setAttribute('data-year', year);
      option.setAttribute('data-tmdb', tmdbId);
      fragment.appendChild(option);
    });
    elements.movieOptions.appendChild(fragment);
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
    if (isStandaloneEnabled()) {
      elements.movieNotFoundPrompt.setAttribute('hidden', 'hidden');
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

  function isStandaloneEnabled() {
    return elements.standaloneCheckbox ? elements.standaloneCheckbox.checked : false;
  }

  function isStandaloneCustomNameEnabled() {
    if (!isStandaloneEnabled()) {
      return false;
    }
    return elements.standaloneCustomToggle
      ? elements.standaloneCustomToggle.checked
      : false;
  }

  function updateStandaloneCustomNameState() {
    const customEnabled = isStandaloneCustomNameEnabled();
    if (elements.standaloneCustomGroup) {
      if (customEnabled) {
        elements.standaloneCustomGroup.removeAttribute('hidden');
      } else {
        elements.standaloneCustomGroup.setAttribute('hidden', 'hidden');
      }
    }
    if (elements.standaloneCustomInput) {
      elements.standaloneCustomInput.disabled = !customEnabled;
      if (customEnabled) {
        elements.standaloneCustomInput.setAttribute('required', 'required');
      } else {
        elements.standaloneCustomInput.removeAttribute('required');
        if (!isStandaloneEnabled()) {
          elements.standaloneCustomInput.value = '';
        }
      }
    }
  }

  function updateStandaloneNamingVisibility() {
    const standaloneEnabled = isStandaloneEnabled();
    if (elements.standaloneNamingOptions) {
      if (standaloneEnabled) {
        elements.standaloneNamingOptions.removeAttribute('hidden');
      } else {
        elements.standaloneNamingOptions.setAttribute('hidden', 'hidden');
      }
    }
    if (!standaloneEnabled && elements.standaloneCustomToggle) {
      elements.standaloneCustomToggle.checked = false;
    }
    updateStandaloneCustomNameState();
  }

  function updateStandaloneState() {
    const enabled = isStandaloneEnabled();

    if (elements.movieNameInput) {
      elements.movieNameInput.disabled = enabled;
      elements.movieNameInput.required = !enabled;
      if (enabled) {
        elements.movieNameInput.value = '';
        elements.movieNameInput.removeAttribute('required');
      } else {
        elements.movieNameInput.setAttribute('required', 'required');
      }
    }
    if (elements.movieIdInput) {
      elements.movieIdInput.value = '';
    }
    if (elements.titleInput && enabled) {
      elements.titleInput.value = '';
    }
    if (elements.yearInput && enabled) {
      elements.yearInput.value = '';
    }
    if (elements.tmdbInput && enabled) {
      elements.tmdbInput.value = '';
    }

    if (elements.extraCheckbox) {
      if (enabled && elements.extraCheckbox.checked) {
        elements.extraCheckbox.checked = false;
      }
      elements.extraCheckbox.disabled = enabled;
    }

    updateExtraVisibility();
    updateMovieNotFoundPrompt();
    updateStandaloneNamingVisibility();

    if (elements.movieFeedback) {
      elements.movieFeedback.setAttribute('hidden', 'hidden');
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
    refreshModalOpenState();
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
    state.addMovie.modalOpen = false;
    refreshModalOpenState();
    resetAddMovieState();
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

  async function refreshMovieLibrary() {
    if (!elements.refreshLibraryButton) {
      return;
    }
    const button = elements.refreshLibraryButton;
    const originalLabel = button.textContent;
    button.disabled = true;
    button.textContent = 'Refreshing…';
    setMovieFeedback('Refreshing Radarr movie library…', 'info');
    try {
      const response = await fetch('/radarr/movies/refresh', { method: 'POST' });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const message = data && data.error
          ? data.error
          : `Failed to refresh Radarr library (HTTP ${response.status}).`;
        setMovieFeedback(message, 'error');
        return;
      }
      const movies = Array.isArray(data.movies) ? data.movies : [];
      replaceMovieOptions(movies);
      syncMovieSelection();
      const count = movies.length;
      if (count > 0) {
        const label = count === 1 ? 'movie' : 'movies';
        setMovieFeedback(`Loaded ${count} ${label} from Radarr.`, 'success');
      } else {
        setMovieFeedback('No movies were returned from Radarr.', 'warning');
      }
      appendConsoleLine('Refreshed Radarr movie library.');
    } catch (err) {
      const message = err && err.message ? err.message : String(err || 'Unknown error');
      setMovieFeedback(`Failed to refresh Radarr library: ${message}`, 'error');
    } finally {
      button.disabled = false;
      button.textContent = originalLabel || 'Refresh Library';
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

  function formatYouTubeViewCount(count) {
    const value = Number(count);
    if (!Number.isFinite(value) || value < 0) {
      return '';
    }
    const thresholds = [
      { limit: 1e12, suffix: 'T' },
      { limit: 1e9, suffix: 'B' },
      { limit: 1e6, suffix: 'M' },
      { limit: 1e3, suffix: 'K' }
    ];
    for (const { limit, suffix } of thresholds) {
      if (value >= limit) {
        const scaled = value / limit;
        const display = scaled >= 10 ? Math.round(scaled) : Math.round(scaled * 10) / 10;
        return `${display}${suffix} views`;
      }
    }
    return `${Math.round(value).toLocaleString()} views`;
  }

  function formatYouTubeDuration(seconds) {
    const total = Number(seconds);
    if (!Number.isFinite(total) || total <= 0) {
      return '';
    }
    const wholeSeconds = Math.round(total);
    const hours = Math.floor(wholeSeconds / 3600);
    const minutes = Math.floor((wholeSeconds % 3600) / 60);
    const secs = wholeSeconds % 60;
    if (hours > 0) {
      return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    }
    return `${minutes}:${String(secs).padStart(2, '0')}`;
  }

  function setYouTubeSearchStatus(message, tone = '') {
    if (!elements.youtubeSearchStatus) {
      return;
    }
    const status = elements.youtubeSearchStatus;
    status.textContent = message || '';
    status.className = 'modal-status';
    if (tone) {
      status.classList.add(`is-${tone}`);
    }
  }

  function clearYouTubeSearchTimer() {
    if (state.youtubeSearch.searchTimeout) {
      clearTimeout(state.youtubeSearch.searchTimeout);
      state.youtubeSearch.searchTimeout = null;
    }
  }

  function resetYouTubeSearchState() {
    clearYouTubeSearchTimer();
    state.youtubeSearch.loading = false;
    state.youtubeSearch.results = [];
    state.youtubeSearch.selectedIndex = -1;
    state.youtubeSearch.query = '';
    state.youtubeSearch.searchToken = 0;
    setYouTubeSearchStatus('');
    renderYouTubeSearchResults();
  }

  function renderYouTubeSearchResults() {
    if (!elements.youtubeSearchResults) {
      return;
    }
    const list = elements.youtubeSearchResults;
    const results = Array.isArray(state.youtubeSearch.results) ? state.youtubeSearch.results : [];
    list.innerHTML = '';
    if (!results.length) {
      list.setAttribute('hidden', 'hidden');
      return;
    }
    list.removeAttribute('hidden');
    results.forEach((video, index) => {
      const item = document.createElement('li');
      item.className = 'modal-result';
      if (index === state.youtubeSearch.selectedIndex) {
        item.classList.add('is-selected');
      }
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'modal-result-button';
      button.dataset.index = String(index);

      const title = document.createElement('span');
      title.className = 'result-title';
      title.textContent = (video.title || '').trim() || 'YouTube Video';
      button.appendChild(title);

      const metaParts = [];
      if (video.uploader) {
        metaParts.push(video.uploader);
      }
      const viewsLabel = formatYouTubeViewCount(video.viewCount);
      if (viewsLabel) {
        metaParts.push(viewsLabel);
      }
      const durationLabel = formatYouTubeDuration(video.duration);
      if (durationLabel) {
        metaParts.push(durationLabel);
      }
      if (metaParts.length) {
        const meta = document.createElement('span');
        meta.className = 'result-meta';
        meta.textContent = metaParts.join(' • ');
        button.appendChild(meta);
      }

      item.appendChild(button);
      list.appendChild(item);
    });
  }

  function focusYouTubeSearchSelection() {
    if (!elements.youtubeSearchResults) {
      return;
    }
    const index = state.youtubeSearch.selectedIndex;
    if (!Number.isInteger(index) || index < 0) {
      return;
    }
    const selector = `.modal-result-button[data-index="${index}"]`;
    const button = elements.youtubeSearchResults.querySelector(selector);
    if (button && typeof button.focus === 'function') {
      button.focus();
      if (typeof button.scrollIntoView === 'function') {
        button.scrollIntoView({ block: 'nearest' });
      }
    }
  }

  function changeYouTubeSearchSelection(offset) {
    const results = Array.isArray(state.youtubeSearch.results) ? state.youtubeSearch.results : [];
    if (!results.length || !Number.isInteger(offset) || offset === 0) {
      return;
    }
    const current = Number.isInteger(state.youtubeSearch.selectedIndex) && state.youtubeSearch.selectedIndex >= 0
      ? state.youtubeSearch.selectedIndex
      : 0;
    let next = current + offset;
    if (next < 0) {
      next = results.length - 1;
    } else if (next >= results.length) {
      next = 0;
    }
    state.youtubeSearch.selectedIndex = next;
    renderYouTubeSearchResults();
    focusYouTubeSearchSelection();
  }

  function openYouTubeSearchModal(initialQuery = '') {
    if (!elements.youtubeSearchModal) {
      return;
    }
    state.youtubeSearch.modalOpen = true;
    state.youtubeSearch.lastFocusedElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    state.youtubeSearch.searchToken = 0;
    elements.youtubeSearchModal.removeAttribute('hidden');
    refreshModalOpenState();
    resetYouTubeSearchState();
    const query = (initialQuery || '').trim();
    if (elements.youtubeSearchInput) {
      elements.youtubeSearchInput.value = query;
      elements.youtubeSearchInput.focus();
      if (query.length >= 2) {
        scheduleYouTubeSearch({ immediate: true });
      } else {
        setYouTubeSearchStatus('Enter at least 2 characters to search YouTube.', 'info');
      }
    } else {
      setYouTubeSearchStatus('Enter at least 2 characters to search YouTube.', 'info');
    }
  }

  function closeYouTubeSearchModal(options = {}) {
    if (!elements.youtubeSearchModal || !state.youtubeSearch.modalOpen) {
      return;
    }
    elements.youtubeSearchModal.setAttribute('hidden', 'hidden');
    state.youtubeSearch.modalOpen = false;
    refreshModalOpenState();
    const restoreFocus = options.restoreFocus !== false;
    const lastFocused = state.youtubeSearch.lastFocusedElement;
    state.youtubeSearch.lastFocusedElement = null;
    resetYouTubeSearchState();
    if (restoreFocus && lastFocused && typeof lastFocused.focus === 'function') {
      lastFocused.focus();
    }
  }

  function scheduleYouTubeSearch(options = {}) {
    if (!elements.youtubeSearchInput) {
      return;
    }
    const query = elements.youtubeSearchInput.value ? elements.youtubeSearchInput.value.trim() : '';
    state.youtubeSearch.query = query;
    clearYouTubeSearchTimer();
    if (!query || query.length < 2) {
      state.youtubeSearch.results = [];
      state.youtubeSearch.selectedIndex = -1;
      renderYouTubeSearchResults();
      setYouTubeSearchStatus('Enter at least 2 characters to search YouTube.', 'info');
      return;
    }
    if (options.immediate) {
      performYouTubeSearch(query);
      return;
    }
    state.youtubeSearch.searchTimeout = setTimeout(() => {
      performYouTubeSearch(query);
    }, 250);
  }

  async function performYouTubeSearch(query) {
    const token = ++state.youtubeSearch.searchToken;
    state.youtubeSearch.loading = true;
    setYouTubeSearchStatus('Searching YouTube…', 'info');
    try {
      const params = new URLSearchParams({ q: query, limit: '10' });
      const response = await fetch(`/youtube/search?${params.toString()}`);
      const data = await response.json().catch(() => ({}));
      if (token !== state.youtubeSearch.searchToken) {
        return;
      }
      if (!response.ok) {
        const message = data && data.error ? data.error : `Search failed (HTTP ${response.status}).`;
        state.youtubeSearch.results = [];
        state.youtubeSearch.selectedIndex = -1;
        renderYouTubeSearchResults();
        setYouTubeSearchStatus(message, 'error');
        return;
      }
      const results = Array.isArray(data.results)
        ? data.results
            .map(item => {
              if (!item || !item.url) {
                return null;
              }
              return {
                id: item.id || '',
                title: item.title || 'YouTube Video',
                url: item.url,
                uploader: item.uploader || '',
                viewCount: typeof item.viewCount === 'number' ? item.viewCount : null,
                duration: typeof item.duration === 'number' ? item.duration : null
              };
            })
            .filter(Boolean)
        : [];
      state.youtubeSearch.results = results;
      state.youtubeSearch.selectedIndex = results.length ? 0 : -1;
      renderYouTubeSearchResults();
      if (!results.length) {
        setYouTubeSearchStatus('No videos matched that search.', 'warning');
      } else {
        setYouTubeSearchStatus('Select a result to use its URL.', 'success');
      }
    } catch (err) {
      if (token !== state.youtubeSearch.searchToken) {
        return;
      }
      state.youtubeSearch.results = [];
      state.youtubeSearch.selectedIndex = -1;
      renderYouTubeSearchResults();
      const message = err && err.message ? err.message : err;
      setYouTubeSearchStatus(`Failed to search YouTube: ${message}`, 'error');
    } finally {
      if (token === state.youtubeSearch.searchToken) {
        state.youtubeSearch.loading = false;
      }
    }
  }

  function selectYouTubeSearchResult(index) {
    const results = Array.isArray(state.youtubeSearch.results) ? state.youtubeSearch.results : [];
    const numericIndex = Number(index);
    if (!Number.isInteger(numericIndex) || numericIndex < 0 || numericIndex >= results.length) {
      return;
    }
    state.youtubeSearch.selectedIndex = numericIndex;
    renderYouTubeSearchResults();
    applyYouTubeSearchSelection(results[numericIndex]);
  }

  function applyYouTubeSearchSelection(video) {
    if (!video || !elements.ytInput) {
      return;
    }
    const url = video.url && typeof video.url === 'string' ? video.url.trim() : '';
    if (!url) {
      setYouTubeSearchStatus('Unable to use that result because it does not include a URL.', 'error');
      return;
    }
    elements.ytInput.value = url;
    try {
      const inputEvent = new Event('input', { bubbles: true });
      elements.ytInput.dispatchEvent(inputEvent);
    } catch (err) {
      // Ignore event dispatch failures (e.g., older browsers)
    }
    closeYouTubeSearchModal({ restoreFocus: false });
    elements.ytInput.focus();
    if (typeof elements.ytInput.select === 'function') {
      elements.ytInput.select();
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

    if (entry.message && (entry.status === 'failed' || entry.status === 'cancelled')) {
      const message = document.createElement('div');
      message.className = 'download-error';
      message.textContent = entry.message;
      wrapper.appendChild(message);
    }

    const footer = document.createElement('div');
    footer.className = 'item-footer';

    const footerLeft = document.createElement('span');
    footerLeft.className = 'item-footer-left';
    const metadataText = (entry.metadata || []).filter(Boolean).join(' • ');
    footerLeft.textContent = metadataText || ' ';
    footer.appendChild(footerLeft);

    const footerRight = document.createElement('div');
    footerRight.className = 'item-footer-right';

    if (entry.id && CANCELLABLE_STATUSES.has(entry.status)) {
      const cancelButton = document.createElement('button');
      cancelButton.type = 'button';
      cancelButton.className = 'download-cancel-button';
      cancelButton.dataset.jobId = entry.id;
      const isCancelling = state.pendingCancellations.has(entry.id);
      cancelButton.textContent = isCancelling ? 'Cancelling…' : 'Cancel';
      cancelButton.disabled = isCancelling;
      const cancelLabel = entry.label ? `Cancel ${entry.label}` : 'Cancel download';
      cancelButton.setAttribute('aria-label', cancelLabel);
      footerRight.appendChild(cancelButton);
    }

    const timestampElem = document.createElement('span');
    timestampElem.className = 'item-timestamp';
    const timestamp = entry.status === 'complete' || entry.status === 'failed' || entry.status === 'cancelled'
      ? formatTime(entry.updatedAt || entry.completedAt)
      : formatTime(entry.startedAt);
    timestampElem.textContent = timestamp;
    footerRight.appendChild(timestampElem);
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
        state.pendingCancellations.delete(jobId);
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
      if (job.status === 'complete' || job.status === 'failed' || job.status === 'cancelled') {
        state.pendingCancellations.delete(jobId);
        stopJobPolling(jobId);
      }
    } catch (err) {
      appendConsoleLine(`ERROR: Failed to poll job ${jobId}: ${err && err.message ? err.message : err}`, 'error');
      stopJobPolling(jobId);
      state.pendingCancellations.delete(jobId);
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

  async function cancelJob(jobId) {
    if (!jobId || state.pendingCancellations.has(jobId)) {
      return;
    }
    state.pendingCancellations.add(jobId);
    renderDownloads();
    try {
      const response = await fetch(`/jobs/${encodeURIComponent(jobId)}/cancel`, {
        method: 'POST'
      });
      let data = null;
      try {
        data = await response.json();
      } catch (err) {
        data = null;
      }
      if (!response.ok) {
        const message = data && data.message
          ? data.message
          : data && data.error
            ? data.error
            : `HTTP ${response.status}`;
        appendConsoleLine(`ERROR: Failed to cancel job ${jobId}: ${message}`, 'error');
        state.pendingCancellations.delete(jobId);
        renderDownloads();
        return;
      }
      if (data && data.message) {
        appendConsoleLine(data.message);
      }
      const job = data && data.job ? normaliseJob(data.job) : null;
      if (job) {
        upsertDownload(job);
      } else {
        renderDownloads();
      }
      startJobPolling(jobId);
    } catch (err) {
      appendConsoleLine(`ERROR: Failed to cancel job ${jobId}: ${err && err.message ? err.message : err}`, 'error');
      state.pendingCancellations.delete(jobId);
      renderDownloads();
    }
  }

  function upsertDownload(update) {
    if (!update || !update.id) {
      return;
    }
    const index = state.downloads.findIndex(item => item.id === update.id);
    let updatedEntry = null;
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
      updatedEntry = state.downloads[index];
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
      updatedEntry = state.downloads[state.downloads.length - 1];
    }

    if (updatedEntry && updatedEntry.id && !CANCELLABLE_STATUSES.has(updatedEntry.status)) {
      state.pendingCancellations.delete(updatedEntry.id);
    }

    state.downloads.sort((a, b) => {
      const left = (a.startedAt instanceof Date ? a.startedAt : parseDate(a.startedAt)) || new Date(0);
      const right = (b.startedAt instanceof Date ? b.startedAt : parseDate(b.startedAt)) || new Date(0);
      return right.getTime() - left.getTime();
    });

    if (state.downloads.length > MAX_DOWNLOAD_ENTRIES) {
      const removed = state.downloads.splice(MAX_DOWNLOAD_ENTRIES);
      removed.forEach(entry => {
        stopJobPolling(entry.id);
        if (entry && entry.id) {
          state.pendingCancellations.delete(entry.id);
        }
      });
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
        if (entry && CANCELLABLE_STATUSES.has(entry.status)) {
          startJobPolling(entry.id);
        }
      });
    } catch (err) {
      appendConsoleLine(`ERROR: Failed to load job history: ${err && err.message ? err.message : err}`, 'error');
    }
  }

  if (elements.refreshLibraryButton) {
    elements.refreshLibraryButton.addEventListener('click', () => {
      refreshMovieLibrary();
    });
  }

  if (elements.movieNameInput) {
    elements.movieNameInput.addEventListener('input', handleMovieNameInput);
    elements.movieNameInput.addEventListener('change', handleMovieNameInput);
  }

  if (elements.standaloneCheckbox) {
    elements.standaloneCheckbox.addEventListener('change', updateStandaloneState);
  }

  if (elements.standaloneCustomToggle) {
    elements.standaloneCustomToggle.addEventListener('change', updateStandaloneCustomNameState);
  }

  initialiseMovieNotFoundPrompt();
  syncMovieSelection();

  if (elements.youtubeSearchButton) {
    elements.youtubeSearchButton.addEventListener('click', () => {
      const currentValue = elements.ytInput ? elements.ytInput.value.trim() : '';
      const initialQuery = currentValue && !YOUTUBE_URL_PATTERN.test(currentValue) ? currentValue : '';
      openYouTubeSearchModal(initialQuery);
    });
  }

  if (elements.youtubeSearchBackdrop) {
    elements.youtubeSearchBackdrop.addEventListener('click', () => {
      closeYouTubeSearchModal();
    });
  }

  if (elements.youtubeSearchCloseButtons && typeof elements.youtubeSearchCloseButtons.forEach === 'function') {
    elements.youtubeSearchCloseButtons.forEach(button => {
      button.addEventListener('click', () => {
        closeYouTubeSearchModal();
      });
    });
  }

  if (elements.youtubeSearchInput) {
    elements.youtubeSearchInput.addEventListener('input', () => {
      scheduleYouTubeSearch();
    });
    elements.youtubeSearchInput.addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault();
        const results = Array.isArray(state.youtubeSearch.results) ? state.youtubeSearch.results : [];
        if (results.length && state.youtubeSearch.selectedIndex >= 0) {
          selectYouTubeSearchResult(state.youtubeSearch.selectedIndex);
        } else {
          scheduleYouTubeSearch({ immediate: true });
        }
      } else if (event.key === 'ArrowDown') {
        event.preventDefault();
        changeYouTubeSearchSelection(1);
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        changeYouTubeSearchSelection(-1);
      }
    });
  }

  if (elements.youtubeSearchSubmit) {
    elements.youtubeSearchSubmit.addEventListener('click', () => {
      scheduleYouTubeSearch({ immediate: true });
    });
  }

  if (elements.youtubeSearchResults) {
    elements.youtubeSearchResults.addEventListener('click', event => {
      const button = event.target instanceof Element ? event.target.closest('.modal-result-button') : null;
      if (!button) {
        return;
      }
      const index = button.dataset ? button.dataset.index : null;
      if (index === null || index === undefined) {
        return;
      }
      event.preventDefault();
      selectYouTubeSearchResult(Number(index));
    });
    elements.youtubeSearchResults.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        changeYouTubeSearchSelection(1);
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        changeYouTubeSearchSelection(-1);
      } else if (event.key === 'Enter') {
        const button = event.target instanceof Element ? event.target.closest('.modal-result-button') : null;
        if (button && button.dataset && button.dataset.index !== undefined) {
          event.preventDefault();
          selectYouTubeSearchResult(Number(button.dataset.index));
        }
      }
    });
  }

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
    if (event.key !== 'Escape') {
      return;
    }
    if (state.youtubeSearch.modalOpen) {
      event.preventDefault();
      closeYouTubeSearchModal();
      return;
    }
    if (state.addMovie.modalOpen) {
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
      const cancelButton = event.target instanceof Element ? event.target.closest('.download-cancel-button') : null;
      if (cancelButton) {
        const jobId = cancelButton.dataset ? cancelButton.dataset.jobId : null;
        if (jobId) {
          event.preventDefault();
          event.stopPropagation();
          cancelJob(jobId);
        }
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

    elements.downloadsList.addEventListener('keydown', event => {
      if (event.defaultPrevented) {
        return;
      }
      if (event.key !== 'Enter' && event.key !== ' ') {
        return;
      }
      if (event.target instanceof Element && event.target.closest('.download-cancel-button')) {
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

    const standaloneEnabled = elements.standaloneCheckbox
      ? elements.standaloneCheckbox.checked
      : false;
    const standaloneCustomEnabled = standaloneEnabled && elements.standaloneCustomToggle
      ? elements.standaloneCustomToggle.checked
      : false;
    const standaloneCustomName = elements.standaloneCustomInput
      ? elements.standaloneCustomInput.value.trim()
      : '';

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
      playlist_mode: elements.playlistModeSelect ? elements.playlistModeSelect.value : 'single',
      standalone: standaloneEnabled,
      standalone_name_mode: standaloneEnabled
        ? standaloneCustomEnabled
          ? 'custom'
          : 'youtube'
        : 'youtube',
      standalone_custom_name: standaloneEnabled && standaloneCustomEnabled
        ? standaloneCustomName
        : ''
    };

    resetConsole('Submitting request...');

    const errors = [];
    if (!payload.yturl) {
      errors.push('YouTube URL is required.');
    } else if (!YOUTUBE_URL_PATTERN.test(payload.yturl)) {
      errors.push('Please enter a valid YouTube URL.');
    }
    if (!payload.movieId && !payload.standalone) {
      errors.push('Please select a valid movie from the list.');
    }
    if (payload.extra && !payload.extra_name) {
      errors.push('Please provide an extra name.');
    }

    if (payload.standalone) {
      payload.extra = false;
      payload.extra_name = '';
      payload.extraType = 'other';
      if (payload.standalone_name_mode === 'custom' && !payload.standalone_custom_name) {
        errors.push('Please provide a custom name for the standalone download.');
      }
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

  setConsoleVisibility(initialConsoleVisible, { skipStorage: true });
  setDebugMode(initialDebugMode);
  updateStandaloneState();
  renderDownloads();
  loadInitialJobs();
});
