/* github-zoo — shared state and helpers.
   Loaded first: everything else assumes h(), ago() and the label tables. */

'use strict';

var KIND_ICON = { pr: '🔀', issue: '🐛', discussion: '💬' };
var KIND_NAME = { pr: 'PR', issue: 'issue', discussion: 'обсуждение' };

var GROUPS = [
  { key: 'you', title: 'Твой ход', hint: 'без тебя не сдвинется' },
  { key: 'them', title: 'Ждём других', hint: 'твоя часть сделана' },
  { key: 'done', title: 'Решено', hint: 'закрыто за последние дни' },
  { key: 'idle', title: 'Тихо', hint: 'черновики и объявления' },
  { key: 'archive', title: 'Старее месяца', hint: 'порог ignore_days в конфиге' }
];

/* Reasons that should read as pressure rather than description. The rest are
   neutral: a board where everything is highlighted highlights nothing. */
var HOT = { threads: 1, changes: 1, requested: 1, unanswered: 1, reply: 1, repushed: 1 };
var BAD = { ci: 1, incomplete: 1 };
var GOOD = { approved: 1, reviewed: 1, answered: 1, merged: 1, closed: 1 };

var state = null;
var view = {
  search: '',
  kinds: {},        /* pr/issue/discussion — empty means all */
  repo: '',         /* the active project tab; '' is all of them */
  unreadOnly: false,
  showHidden: false,
  /* Folded by default: neither is a task. The archive is what ignore_days put
     away, and the orphan notifications are almost always about things that
     were already merged — both are worth a count, not worth a screen. */
  collapsed: { archive: true, orphans: true, idle: false, them: false, you: false }
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

/* Everything the filters allow *except* the project tab. The tabs' own counts
   come from here: counting after the tab filter would leave every other tab
   showing zero the moment one was picked. */
function filteredExceptProject() {
  if (!state || !state.items) return [];
  var q = view.search.trim().toLowerCase();
  var kinds = Object.keys(view.kinds).filter(function (k) { return view.kinds[k]; });

  return state.items.filter(function (it) {
    if (it.hidden && !view.showHidden) return false;
    if (view.unreadOnly && !(it.verdict && it.verdict.unread)) return false;
    if (kinds.length && kinds.indexOf(it.kind) < 0) return false;
    if (q) {
      var hay = (it.repo + '#' + it.number + ' ' + it.title + ' ' + it.author).toLowerCase();
      if (hay.indexOf(q) < 0) return false;
    }
    return true;
  });
}

function visibleItems() {
  var items = filteredExceptProject();
  if (!view.repo) return items;
  return items.filter(function (it) { return it.repo === view.repo; });
}

/* One entry per project, newest activity first.
   `waiting` counts only what is waiting on you, because that is the number
   worth putting on a tab — a project with forty items you are not blocking is
   quieter than one with a single unanswered question. Items past ignore_days
   are counted in `total` but cannot make a project look fresh: their age is
   exactly why they were folded away. */
function projects() {
  var map = {};
  filteredExceptProject().forEach(function (it) {
    var p = map[it.repo] || (map[it.repo] = { repo: it.repo, fresh: 0, waiting: 0, total: 0 });
    p.total++;
    var turn = (it.verdict && it.verdict.turn) || 'idle';
    if (turn === 'you') p.waiting++;
    if (turn !== 'archive' && (it.updated || 0) > p.fresh) p.fresh = it.updated || 0;
  });
  return Object.keys(map).map(function (k) { return map[k]; }).sort(function (a, b) {
    return b.fresh - a.fresh || b.waiting - a.waiting || a.repo.localeCompare(b.repo);
  });
}

function byGroup(items) {
  var out = { you: [], them: [], done: [], idle: [], archive: [] };
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

/* ---------- favicon ---------- */

/* The tab sits in the background most of the day, so this icon is the part of
   the dashboard actually being looked at. It stays the octopus — that is what
   the tab is recognised by — and only changes colour: blue when nothing is
   waiting on you, orange when something is.

   The octopus is the emoji glyph with a flat colour composited through it
   (`source-in` keeps the fill only where the glyph is opaque). Drawing one by
   hand was tried and came out looking like a ghost; the real glyph still reads
   as an octopus at the 16px a browser draws. */
var FAVICON_IDLE = '#4b8fd9';
/* Amber-orange rather than pure yellow: at 16px, on a light tab strip, yellow
   washes out into the background. */
var FAVICON_WAITING = '#e8942a';
var FAVICON_FONT = '"Apple Color Emoji","Noto Color Emoji","Segoe UI Emoji",sans-serif';

function paintFavicon(count) {
  count = count || 0;
  if (paintFavicon.last === count) return;
  paintFavicon.last = count;

  var size = 64;
  var canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  var ctx = canvas.getContext('2d');
  if (!ctx) return;
  var colour = count ? FAVICON_WAITING : FAVICON_IDLE;

  ctx.font = Math.round(size * 0.86) + 'px ' + FAVICON_FONT;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('🐙', size / 2, size / 2 + size * 0.04);
  ctx.globalCompositeOperation = 'source-in';
  ctx.fillStyle = colour;
  ctx.fillRect(0, 0, size, size);
  ctx.globalCompositeOperation = 'source-over';

  // A machine with no emoji font draws nothing at all, and an invisible icon
  // says less than a plain dot. Check rather than assume.
  if (!_hasInk(ctx, size)) {
    ctx.fillStyle = colour;
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, size / 2 - 2, 0, Math.PI * 2);
    ctx.fill();
  }

  var link = document.querySelector('link[rel="icon"]');
  if (!link) {
    link = document.createElement('link');
    link.rel = 'icon';
    document.head.appendChild(link);
  }
  link.type = 'image/png';
  link.href = canvas.toDataURL('image/png');
}

function _hasInk(ctx, size) {
  try {
    var data = ctx.getImageData(0, 0, size, size).data;
    for (var i = 3; i < data.length; i += 4) {
      if (data[i] > 0) return true;
    }
  } catch (err) {
    return true;  // canvas locked down: assume it drew, rather than replacing it
  }
  return false;
}

/* The same number in the title, so a narrowed tab strip still says it. */
function setTitle(count) {
  var base = document.title.replace(/^\(\d+\+?\)\s*/, '');
  document.title = count ? '(' + (count > 99 ? '99+' : count) + ') ' + base : base;
}

function showWaiting(count) {
  paintFavicon(count);
  setTitle(count);
}

function toast(text, kind) {
  var box = document.getElementById('alert');
  box.className = 'alert' + (kind ? ' ' + kind : '');
  box.textContent = text;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(function () { box.classList.add('hidden'); }, 6000);
}
