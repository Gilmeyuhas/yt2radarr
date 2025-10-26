document.addEventListener('DOMContentLoaded', function() {
  const form = document.getElementById('movieForm');
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
  const extraNameGroup = document.getElementById('extraNameGroup');
  const extraNameInput = document.getElementById('extra_name');
  const consoleDiv = document.getElementById('console');

  function appendConsoleLine(text, isError) {
    const lineElem = document.createElement('div');
    if (isError) {
      lineElem.classList.add('error-line');
    }
    lineElem.textContent = text;
    consoleDiv.appendChild(lineElem);
  }

  function resetConsole(message) {
    consoleDiv.innerHTML = '';
    if (message) {
      appendConsoleLine(message, false);
    }
  }

  function updateExtraVisibility() {
    if (extraCheckbox.checked) {
      extraNameGroup.style.display = 'flex';
      extraNameInput.required = true;
    } else {
      extraNameGroup.style.display = 'none';
      extraNameInput.required = false;
      extraNameInput.value = '';
    }
  }

  extraCheckbox.addEventListener('change', updateExtraVisibility);

  movieNameInput.addEventListener('input', function() {
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

  form.addEventListener('submit', function(e) {
    e.preventDefault();

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
        consoleDiv.innerHTML = '';
        if (data.logs && data.logs.length > 0) {
          data.logs.forEach(line => {
            const isError = line.startsWith('ERROR');
            appendConsoleLine(line, isError);
          });
        } else {
          appendConsoleLine('No output.', false);
        }
        if (!ok && (!data.logs || !data.logs.some(line => line.startsWith('ERROR')))) {
          appendConsoleLine('ERROR: Request failed.', true);
        }
        consoleDiv.scrollTop = consoleDiv.scrollHeight;
      })
      .catch(err => {
        consoleDiv.innerHTML = '';
        appendConsoleLine('ERROR: ' + err, true);
      });
  });

  updateExtraVisibility();
});
