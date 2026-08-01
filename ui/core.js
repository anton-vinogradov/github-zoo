/* github-zoo — shared state and helpers.
   Loaded first: everything else assumes h(), ago() and the label tables. */

'use strict';

var KIND_ICON = { pr: '🔀', issue: '🐛', discussion: '💬' };
var KIND_NAME = { pr: 'PR', issue: 'issue', discussion: 'обсуждение' };

var GROUPS = [
  { key: 'you', title: 'Твой ход', hint: 'без тебя не сдвинется' },
  { key: 'them', title: 'Ждём других', hint: 'твоя часть сделана' },
  { key: 'idle', title: 'Тихо', hint: 'черновики и объявления' },
  { key: 'archive', title: 'Архив', hint: 'месяцами без движения' }
];

/* Reasons that should read as pressure rather than description. The rest are
   neutral: a board where everything is highlighted highlights nothing. */
var HOT = { threads: 1, changes: 1, requested: 1, unanswered: 1, reply: 1, repushed: 1 };
var BAD = { ci: 1, incomplete: 1 };
var GOOD = { approved: 1, reviewed: 1, answered: 1 };

var state = null;
var view = {
  search: '',
  kinds: {},        /* pr/issue/discussion — empty means all */
  repos: {},        /* same, by repository */
  unreadOnly: false,
  showHidden: false,
  collapsed: { archive: true, idle: false, them: false, you: false }
};

/* ---------- dom ---------- */

function h(tag, attrs, children) {
  var el = document.createElement(tag);
  if (attrs) Object.keys(attrs).forEach(function (k) {
    if (k === 'class') el.className = attrs[k];
    else if (k === 'text') el.textContent = attrs[k];
    else if (k.slice(0, 2) === 'on') el.addEventListener(k.slice(2), attrs[k]);
    else if (attrs[k] !== null && attrs[k] !== undefined) el.setAttribute(k, attrs[k]);
  });
  (children || []).forEach(function (c) {
    if (c === null || c === undefined) return;
    el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  });
  return el;
}

/* ---------- formatting ---------- */

function plural(n, one, few, many) {
  var mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

function days(n) {
  n = Math.floor(n);
  if (n <= 0) return 'сегодня';
  if (n < 31) return n + ' ' + plural(n, 'день', 'дня', 'дней');
  var m = Math.round(n / 30.4);
  if (m < 18) return m + ' ' + plural(m, 'месяц', 'месяца', 'месяцев');
  var y = (n / 365).toFixed(n < 730 ? 1 : 0);
  return y + ' ' + plural(Math.round(y), 'год', 'года', 'лет');
}

function ago(ts) {
  if (!ts) return '—';
  var sec = Date.now() / 1000 - ts;
  if (sec < 3600) return Math.max(1, Math.round(sec / 60)) + ' мин назад';
  if (sec < 86400) return Math.round(sec / 3600) + ' ч назад';
  return days(sec / 86400) + ' назад';
}

function when(ts) {
  if (!ts) return '—';
  var d = new Date(ts * 1000);
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: '2-digit' });
}

/* ---------- selection ---------- */

function visibleItems() {
  if (!state || !state.items) return [];
  var q = view.search.trim().toLowerCase();
  var kinds = Object.keys(view.kinds).filter(function (k) { return view.kinds[k]; });
  var repos = Object.keys(view.repos).filter(function (k) { return view.repos[k]; });

  return state.items.filter(function (it) {
    if (it.hidden && !view.showHidden) return false;
    if (view.unreadOnly && !(it.verdict && it.verdict.unread)) return false;
    if (kinds.length && kinds.indexOf(it.kind) < 0) return false;
    if (repos.length && repos.indexOf(it.repo) < 0) return false;
    if (q) {
      var hay = (it.repo + '#' + it.number + ' ' + it.title + ' ' + it.author).toLowerCase();
      if (hay.indexOf(q) < 0) return false;
    }
    return true;
  });
}

function byGroup(items) {
  var out = { you: [], them: [], idle: [], archive: [] };
  items.forEach(function (it) {
    var turn = (it.verdict && it.verdict.turn) || 'idle';
    (out[turn] || out.idle).push(it);
  });
  return out;
}

/* ---------- server ---------- */

function api(path, body) {
  return fetch(path, {
    method: body ? 'POST' : 'GET',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined
  }).then(function (r) { return r.json(); });
}

function toast(text, kind) {
  var box = document.getElementById('alert');
  box.className = 'alert' + (kind === 'warn' ? ' warn' : '');
  box.textContent = text;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(function () { box.classList.add('hidden'); }, 6000);
}
