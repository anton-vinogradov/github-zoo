/* github-zoo — loading, controls and the refresh timer. */

'use strict';

function load() {
  return api('/api/state').then(function (data) {
    state = data;
    render();
    renderFoot();
    showProblems();
  }).catch(function () {
    toast('дашборд не отвечает', 'warn');
  });
}

function showProblems() {
  var box = document.getElementById('alert');
  var problems = [];
  var age = Date.now() / 1000 - (state.generated || 0);

  /* An error on its own does not say whether what you are looking at is a
     minute or a day old — which is the only thing that decides whether it
     matters. GitHub answers 503 often enough that a stale banner over fresh
     data was the usual case. */
  if (state.error) {
    problems.push(state.error + (state.generated
      ? ' · данные от ' + ago(state.generated) : ''));
  }
  (state.warnings || []).forEach(function (w) { problems.push(w); });

  if (!state.error && state.generated && age > 3600) {
    problems.push('данные устарели: последний удачный опрос ' + ago(state.generated));
  }
  var budget = state.budget || {};
  if (budget.remaining !== null && budget.remaining !== undefined && budget.remaining < 500) {
    problems.push('лимит GitHub на исходе: осталось ' + budget.remaining);
  }

  if (!problems.length) { box.classList.add('hidden'); return; }
  box.className = 'alert ' + (state.error ? 'bad' : 'warn');
  box.textContent = problems.join(' · ');
}

function renderFoot() {
  var bits = [];
  if (state.login) bits.push('как ' + state.login);
  if (state.took) bits.push('опрос ' + state.took + ' с');
  var b = state.budget || {};
  if (b.remaining !== null && b.remaining !== undefined) {
    bits.push('лимит GitHub: ' + b.remaining + '/' + (b.limit || 5000));
  }
  if (state.notification_count) bits.push(state.notification_count + ' уведомлений');
  var v = state.version || {};
  if (v.commit && v.commit !== 'unknown') {
    bits.push('версия ' + v.commit + (v.dirty ? ' (правки вне коммита)' : ''));
  }
  if (state.restored) bits.push('показан снимок с прошлого запуска');
  document.getElementById('foot-note').textContent = bits.join(' · ');
}

/* ---------- controls ---------- */

function wire() {
  document.getElementById('search').addEventListener('input', function (e) {
    view.search = e.target.value;
    render();
  });

  document.getElementById('btn-unread').addEventListener('click', function (e) {
    view.unreadOnly = !view.unreadOnly;
    e.target.classList.toggle('on', view.unreadOnly);
    render();
  });

  document.getElementById('btn-hidden').addEventListener('click', function (e) {
    view.showHidden = !view.showHidden;
    e.target.classList.toggle('on', view.showHidden);
    render();
  });

  document.getElementById('btn-refresh').addEventListener('click', function (e) {
    e.target.disabled = true;
    api('/api/refresh', {}).then(function () {
      toast('опрашиваю GitHub…');
      // The poll takes a few seconds; poll the state until it moves rather
      // than guessing a delay.
      var before = state && state.generated;
      var tries = 0;
      var timer = setInterval(function () {
        load().then(function () {
          if ((state && state.generated) !== before || ++tries > 20) {
            clearInterval(timer);
            e.target.disabled = false;
          }
        });
      }, 1500);
    }).catch(function () {
      toast('не получилось', 'warn');
      e.target.disabled = false;
    });
  });

  document.getElementById('btn-theme').addEventListener('click', function () {
    var next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('gz-theme', next); } catch (err) { /* private mode */ }
  });

  document.getElementById('modal').addEventListener('click', function (e) {
    if (e.target.id === 'modal' || e.target.closest('[data-close]')) closeModal();
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeModal();
    if (e.key === '/' && document.activeElement.tagName !== 'INPUT') {
      e.preventDefault();
      document.getElementById('search').focus();
    }
  });
}

(function start() {
  try {
    var saved = localStorage.getItem('gz-theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);
  } catch (err) { /* private mode */ }
  wire();
  load();
  setInterval(load, 60000);
})();
