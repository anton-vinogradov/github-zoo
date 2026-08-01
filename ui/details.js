/* github-zoo — the detail view: why this is here, and what is unanswered. */

'use strict';

function openDetails(item) {
  var v = item.verdict || {};
  document.getElementById('modal-title').textContent =
    item.repo + '#' + item.number + ' — ' + item.title;

  var body = document.getElementById('modal-body');
  body.textContent = '';

  body.appendChild(section('почему здесь', [
    h('div', { class: 'why' }, (v.reasons || []).map(function (r) {
      var cls = 'reason' + (HOT[r.code] ? ' hot' : BAD[r.code] ? ' bad' : GOOD[r.code] ? ' good' : '');
      return h('span', { class: cls, text: r.text });
    })),
    h('dl', { class: 'kv' }, [
      h('dt', { text: 'ход' }), h('dd', { text: turnName(v) }),
      h('dt', { text: 'ждёт' }), h('dd', {
        text: days(v.waiting_days || 0) + ' (с ' + when(v.waiting_since) + ')' }),
      h('dt', { text: 'автор' }), h('dd', { text: item.author }),
      h('dt', { text: 'создано' }), h('dd', { text: when(item.created) }),
      h('dt', { text: 'активность' }), h('dd', { text: ago(item.updated) }),
      item.assignees && item.assignees.length
        ? h('dt', { text: 'назначено' }) : null,
      item.assignees && item.assignees.length
        ? h('dd', { text: item.assignees.join(', ') }) : null,
      item.review_requests && item.review_requests.length
        ? h('dt', { text: 'ревью просят у' }) : null,
      item.review_requests && item.review_requests.length
        ? h('dd', { text: item.review_requests.join(', ') }) : null
    ])
  ]));

  var threads = (item.threads || []).filter(function (t) { return !t.resolved; });
  if (threads.length) body.appendChild(section(
    'незакрытые треды (' + threads.length + ')',
    threads.map(function (t) { return renderThread(t, item); })));

  if (item.reviews && item.reviews.length) {
    body.appendChild(section('ревью', [h('dl', { class: 'kv' },
      flatten(item.reviews.slice(-8).map(function (r) {
        return [h('dt', { text: r.login }),
                h('dd', {}, [reviewState(r.state), ' · ', when(r.at)])];
      })))]));
  }

  if (item.ci && item.ci.total) body.appendChild(section('CI', [renderCi(item.ci)]));

  if (item.notifications && item.notifications.length) {
    body.appendChild(section('уведомления', [h('dl', { class: 'kv' },
      flatten(item.notifications.map(function (n) {
        return [h('dt', { text: n.reason }),
                h('dd', { text: ago(n.updated) + (n.unread ? ' · непрочитано' : '') })];
      })))]));
  }

  body.appendChild(h('div', { class: 'row-actions' }, [
    h('a', { class: 'btn', href: item.url, target: '_blank', rel: 'noopener',
             text: 'открыть на GitHub' }),
    item.hidden
      ? h('button', { class: 'btn', text: 'вернуть на доску', onclick: function () {
          act('/api/unhide', { id: item.id }, 'снова показываю'); closeModal();
        } })
      : h('button', { class: 'btn', text: 'отложить на неделю', onclick: function () {
          act('/api/snooze', { id: item.id, hours: 168 }, 'отложено на неделю'); closeModal();
        } })
  ]));

  document.getElementById('modal').classList.remove('hidden');
}

function turnName(v) {
  var names = { you: 'твой', them: 'за другими', idle: 'ничей', archive: 'архив' };
  var out = names[v.turn] || v.turn;
  if (v.turn === 'archive' && v.would_be) out += ' (был бы «' + (names[v.would_be] || v.would_be) + '»)';
  return out;
}

function renderThread(t, item) {
  /* "Waiting on you" here means the same thing the board means by it, so the
     highlight and the card's reason cannot disagree. */
  var mineTurn = item.mine ? t.last_by !== state.login
                           : (t.last_by !== state.login && t.opened_by === state.login);
  return h('div', { class: 'thread' + (mineTurn && !t.outdated ? ' mine-turn' : '') }, [
    h('div', { class: 'path' }, [
      t.path + (t.line ? ':' + t.line : ''),
      t.outdated ? h('span', { class: 'label', text: 'устарел' }) : null
    ]),
    t.excerpt ? h('p', { class: 'excerpt', text: t.excerpt }) : null,
    h('div', { class: 'meta' }, [
      'открыл ' + t.opened_by + ' · последним ' + t.last_by + ' ' + ago(t.last_at) +
      ' · ' + t.count + ' ' + plural(t.count, 'сообщение', 'сообщения', 'сообщений'),
      t.url ? ' · ' : null,
      t.url ? h('a', { href: t.url, target: '_blank', rel: 'noopener', text: 'перейти' }) : null
    ])
  ]);
}

function renderCi(ci) {
  var box = h('div', { class: 'checks' });
  (ci.failed || []).forEach(function (c) {
    box.appendChild(c.url
      ? h('a', { class: 'reason bad', href: c.url, target: '_blank', rel: 'noopener', text: c.name })
      : h('span', { class: 'reason bad', text: c.name }));
  });
  if (ci.pending) box.appendChild(h('span', { class: 'reason', text: 'идёт: ' + ci.pending }));
  var passed = ci.total - (ci.failed || []).length - (ci.pending || 0);
  if (passed > 0) box.appendChild(h('span', { class: 'reason good', text: 'прошло: ' + passed }));
  return box;
}

function reviewState(s) {
  var map = {
    APPROVED: ['одобрил', 'good'], CHANGES_REQUESTED: ['просит правки', 'hot'],
    COMMENTED: ['прокомментировал', ''], DISMISSED: ['отклонено', ''],
    PENDING: ['не отправлено', '']
  };
  var pair = map[s] || [s || '—', ''];
  return h('span', { class: 'reason ' + pair[1], text: pair[0] });
}

function section(title, children) {
  return h('div', { class: 'section' }, [h('h3', { text: title })].concat(children));
}

function flatten(pairs) {
  return pairs.reduce(function (acc, p) { return acc.concat(p); }, []);
}

function closeModal() {
  document.getElementById('modal').classList.add('hidden');
}
