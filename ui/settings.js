/* github-zoo — the settings page.

   Every field's bounds come from the server's schema rather than being written
   twice; the server enforces them anyway, and a page that disagrees with the
   thing enforcing them is worse than one with no limits at all. */

'use strict';

var config = null;
var pending = {};

function load() {
  return api('/api/settings').then(function (data) {
    config = data;
    pending = {};
    render();
    showWaiting((config.summary || {}).you);
  }).catch(function () { toast('сервер не отвечает', 'warn'); });
}

function render() {
  var box = document.getElementById('settings');
  box.textContent = '';
  if (!config) return;

  var sections = [];
  config.schema.forEach(function (field) {
    var last = sections[sections.length - 1];
    if (!last || last.title !== field.section) {
      sections.push({ title: field.section, fields: [field] });
    } else {
      last.fields.push(field);
    }
  });

  sections.forEach(function (s) { box.appendChild(renderSection(s)); });
  box.appendChild(renderHidden());
  box.appendChild(renderAbout());

  document.getElementById('subtitle').textContent =
    config.overridden.length
      ? config.overridden.length + ' ' + plural(config.overridden.length,
          'значение изменено', 'значения изменены', 'значений изменено') + ' против конфига'
      : 'всё как в конфиге на сервере';

  var v = config.version || {};
  document.getElementById('foot-note').textContent =
    [config.config_path, v.commit && v.commit !== 'unknown' ? 'версия ' + v.commit : null]
      .filter(Boolean).join(' · ');
}

function renderSection(section) {
  var rows = section.fields.map(renderField);
  return h('section', { class: 'set-section' },
    [h('h2', { text: section.title })].concat(rows));
}

function renderField(field) {
  var value = config.values[field.key];
  var overridden = config.overridden.indexOf(field.key) >= 0;
  var control = field.type === 'bool' ? boolControl(field, value) : intControl(field, value);

  return h('div', { class: 'set-row' + (overridden ? ' overridden' : '') }, [
    h('div', { class: 'set-label' }, [
      h('label', { text: field.title, for: 'f-' + field.key }),
      field.hint ? h('p', { class: 'set-hint', text: field.hint }) : null
    ]),
    h('div', { class: 'set-control' }, [
      control,
      field.unit ? h('span', { class: 'set-unit', text: field.unit }) : null
    ])
  ]);
}

function intControl(field, value) {
  return h('input', {
    id: 'f-' + field.key, class: 'set-input', type: 'number',
    min: field.min, max: field.max, value: value,
    onchange: function (e) { stage(field, Number(e.target.value)); }
  });
}

function boolControl(field, value) {
  var input = h('input', {
    id: 'f-' + field.key, type: 'checkbox',
    onchange: function (e) { stage(field, e.target.checked); }
  });
  if (value) input.checked = true;
  return h('label', { class: 'set-toggle' }, [input, h('span', { class: 'set-slider' })]);
}

/* Changes save themselves. A settings page with a Save button that people
   forget to press is a settings page that quietly does nothing. */
function stage(field, value) {
  pending[field.key] = value;
  var body = { values: {} };
  body.values[field.key] = value;
  api('/api/settings', body).then(function (r) {
    if (!r) { toast('сервер не ответил', 'warn'); return; }
    if (r.rejected && r.rejected.length) {
      toast(r.rejected.join('; '), 'warn');
    } else {
      var applied = r.summary || {};
      toast('сохранено' + (applied.you !== undefined
        ? ' · твоего хода: ' + applied.you : ''));
    }
    config.values = r.values || config.values;
    return load();
  }).catch(function () { toast('не удалось сохранить', 'warn'); });
}

function renderHidden() {
  var entries = Object.keys(config.hidden || {});
  var rows = entries.map(function (id) {
    var entry = config.hidden[id];
    var until = entry.kind === 'snooze' && entry.until
      ? 'до ' + new Date(entry.until * 1000).toLocaleString('ru-RU',
          { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
      : (entry.kind === 'forever' ? '' : 'до новой активности');
    return h('div', { class: 'set-row' }, [
      h('div', { class: 'set-label' }, [
        h('label', { text: id }),
        h('p', { class: 'set-hint',
                 text: (HIDDEN_LABEL[entry.kind] || entry.kind) + (until ? ' ' + until : '') })
      ]),
      h('div', { class: 'set-control' }, [
        h('button', { class: 'btn btn-small', text: 'вернуть', onclick: function () {
          api('/api/unhide', { id: id }).then(function () {
            toast('вернул на доску');
            return load();
          });
        } })
      ])
    ]);
  });
  if (!rows.length) {
    rows = [h('p', { class: 'set-hint', text: 'ничего не скрыто' })];
  }
  return h('section', { class: 'set-section' },
    [h('h2', { text: 'Скрытые карточки' })].concat(rows));
}

function renderAbout() {
  var b = config.budget || {};
  var rows = [
    ['лимит GitHub', b.remaining !== null && b.remaining !== undefined
      ? b.remaining + ' из ' + (b.limit || 5000) : '—'],
    ['запросов за сессию', String(b.calls || 0)],
    ['повторов из-за сбоев', String(b.retries || 0)],
    ['конфиг', config.config_path || '—'],
    ['Telegram', config.telegram_ready ? 'чат задан в конфиге' : 'чат не задан']
  ];
  return h('section', { class: 'set-section' }, [
    h('h2', { text: 'Состояние' }),
    h('dl', { class: 'kv' }, rows.reduce(function (acc, pair) {
      return acc.concat([h('dt', { text: pair[0] }), h('dd', { text: pair[1] })]);
    }, []))
  ]);
}

/* ---------- controls ---------- */

document.getElementById('btn-selftest').addEventListener('click', function (e) {
  e.target.disabled = true;
  api('/api/selftest').then(function (r) {
    var bits = [];
    bits.push('GitHub: ' + (r.github.ok ? 'ок, ' + r.github.as : r.github.error));
    bits.push('Telegram: ' + (r.telegram.ok ? 'ок' : r.telegram.message));
    bits.push('история: ' + (r.store.ok ? 'ок' : 'недоступна'));
    toast(bits.join(' · '), r.github.ok ? '' : 'warn');
  }).catch(function () { toast('проверка не удалась', 'warn'); })
    .then(function () { e.target.disabled = false; });
});

document.getElementById('btn-reset').addEventListener('click', function () {
  if (!window.confirm('Убрать все переопределения и вернуться к значениям из конфига?')) return;
  api('/api/settings/reset', {}).then(function () {
    toast('вернул значения из конфига');
    return load();
  }).catch(function () { toast('не получилось', 'warn'); });
});

document.getElementById('btn-theme').addEventListener('click', function () {
  var next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', next);
  try { localStorage.setItem('gz-theme', next); } catch (err) { /* private mode */ }
});

(function start() {
  try {
    var saved = localStorage.getItem('gz-theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);
  } catch (err) { /* private mode */ }
  load();
})();
