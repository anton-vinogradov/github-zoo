/* github-zoo — the board: filter chips, groups and cards. */

'use strict';

function renderFilters() {
  var box = document.getElementById('filters');
  box.textContent = '';
  if (!state || !state.items) return;

  var items = state.items.filter(function (it) { return view.showHidden || !it.hidden; });

  var kindCounts = {};
  var repoCounts = {};
  items.forEach(function (it) {
    kindCounts[it.kind] = (kindCounts[it.kind] || 0) + 1;
    repoCounts[it.repo] = (repoCounts[it.repo] || 0) + 1;
  });

  Object.keys(KIND_ICON).forEach(function (kind) {
    if (!kindCounts[kind]) return;
    box.appendChild(chip(KIND_ICON[kind] + ' ' + KIND_NAME[kind], kindCounts[kind],
      view.kinds[kind], function () {
        view.kinds[kind] = !view.kinds[kind];
        render();
      }));
  });

  /* Repositories, busiest first — the long tail of one-issue repositories
     would otherwise push the ones you actually work in off the row. */
  var repos = Object.keys(repoCounts).sort(function (a, b) {
    return repoCounts[b] - repoCounts[a] || a.localeCompare(b);
  });
  var shown = repos.slice(0, 10);
  Object.keys(view.repos).forEach(function (r) {
    if (view.repos[r] && shown.indexOf(r) < 0 && repoCounts[r]) shown.push(r);
  });
  shown.forEach(function (repo) {
    box.appendChild(chip(repo, repoCounts[repo], view.repos[repo], function () {
      view.repos[repo] = !view.repos[repo];
      render();
    }));
  });
  if (repos.length > shown.length) {
    box.appendChild(h('span', { class: 'chip', text: '+' + (repos.length - shown.length) + ' ещё' }));
  }
}

function chip(text, count, on, onclick) {
  return h('span', { class: 'chip' + (on ? ' on' : ''), onclick: onclick },
    [text, h('span', { class: 'n', text: String(count) })]);
}

function render() {
  renderFilters();
  renderSummary();

  var board = document.getElementById('board');
  board.textContent = '';

  var groups = byGroup(visibleItems());
  var any = false;

  GROUPS.forEach(function (g) {
    var items = groups[g.key];
    if (!items.length) return;
    any = true;
    board.appendChild(renderGroup(g, items));
  });

  if (state && state.orphan_notifications && state.orphan_notifications.length) {
    board.appendChild(renderOrphans(state.orphan_notifications));
    any = true;
  }

  if (!any) {
    board.appendChild(h('div', { class: 'empty',
      text: state && state.generated ? 'ничего не найдено — попробуй снять фильтры'
                                     : 'первый опрос ещё идёт…' }));
  }
}

function renderGroup(g, items) {
  var collapsed = view.collapsed[g.key];
  var head = h('div', { class: 'group-head', onclick: function () {
    view.collapsed[g.key] = !view.collapsed[g.key];
    render();
  } }, [
    h('span', { class: 'caret', text: collapsed ? '▶' : '▼' }),
    h('h2', { text: g.title }),
    h('span', { class: 'count', text: String(items.length) }),
    h('span', { class: 'hint', text: g.hint })
  ]);

  var children = [head];
  if (!collapsed) {
    var cards = h('div', { class: 'cards' });
    items.forEach(function (it) { cards.appendChild(renderCard(it)); });
    children.push(cards);
  }
  return h('section', { class: 'group ' + g.key }, children);
}

function renderCard(item) {
  var v = item.verdict || {};
  var turn = v.turn || 'idle';

  var where = h('div', { class: 'where' }, [
    h('span', { class: 'repo', text: item.repo }),
    h('span', { class: 'num', text: '#' + item.number }),
    item.draft ? h('span', { class: 'label', text: 'черновик' }) : null,
    item.private ? h('span', { class: 'label', text: 'private' }) : null,
    item.hidden ? h('span', { class: 'label',
      text: item.hidden === 'mute' ? 'заглушено' : 'отложено' }) : null
  ].concat((item.labels || []).slice(0, 3).map(function (l) {
    return h('span', { class: 'label', text: l.name });
  })));

  var why = h('div', { class: 'why' }, (v.reasons || []).map(function (r) {
    var cls = 'reason' + (HOT[r.code] ? ' hot' : BAD[r.code] ? ' bad' : GOOD[r.code] ? ' good' : '');
    return h('span', { class: cls, text: r.text });
  }));

  var main = h('div', { class: 'main' }, [
    where,
    h('div', { class: 'title', text: item.title }),
    why,
    renderRowActions(item)
  ]);

  var age = h('span', {
    class: 'age' + (v.stale ? ' stale' : v.rotting ? ' rotting' : ''),
    title: 'ход перешёл ' + when(v.waiting_since)
  }, [days(v.waiting_days || 0)]);

  var side = h('div', { class: 'side' }, [
    age,
    v.unread ? h('span', { class: 'badge', text: String(v.unread) }) : null,
    ciDot(item)
  ]);

  return h('article', {
    class: 'card ' + turn + (item.hidden ? ' hidden-item' : ''),
    onclick: function (e) {
      if (e.target.closest('button') || e.target.closest('a')) return;
      openDetails(item);
    }
  }, [h('div', { class: 'kind', text: KIND_ICON[item.kind] || '•' }), main, side]);
}

function ciDot(item) {
  var ci = item.ci;
  if (!ci || !ci.total) return null;
  var cls = ci.failed && ci.failed.length ? 'bad' : ci.pending ? 'warn' : 'ok';
  var title = ci.failed && ci.failed.length
    ? 'CI: упало ' + ci.failed.length + ' из ' + ci.total
    : ci.pending ? 'CI: идёт ' + ci.pending + ' из ' + ci.total
                 : 'CI: зелёный (' + ci.total + ')';
  return h('span', { class: 'dot ' + cls, title: title });
}

function renderRowActions(item) {
  var box = h('div', { class: 'row-actions' });
  box.appendChild(h('a', { class: 'btn btn-small', href: item.url, target: '_blank',
                          rel: 'noopener', text: 'открыть' }));
  if (item.hidden) {
    box.appendChild(h('button', { class: 'btn btn-small', onclick: function () {
      act('/api/unhide', { id: item.id }, 'снова показываю');
    }, text: 'вернуть' }));
    return box;
  }
  box.appendChild(h('button', { class: 'btn btn-small', title: 'скрыть на сутки — вернётся раньше, если что-то изменится',
    onclick: function () { act('/api/snooze', { id: item.id, hours: 24 }, 'отложено на сутки'); },
    text: 'отложить' }));
  box.appendChild(h('button', { class: 'btn btn-small', title: 'скрыть до любого изменения',
    onclick: function () { act('/api/mute', { id: item.id }, 'заглушено'); },
    text: 'заглушить' }));
  return box;
}

function act(path, body, okText) {
  api(path, body).then(function (r) {
    if (r && r.ok) { toast(okText); setTimeout(load, 400); }
    else toast((r && r.error) || 'не получилось', 'warn');
  }).catch(function () { toast('сервер не ответил', 'warn'); });
}

function renderOrphans(notes) {
  var collapsed = view.collapsed.orphans;
  var head = h('div', { class: 'group-head', onclick: function () {
    view.collapsed.orphans = !collapsed;
    render();
  } }, [
    h('span', { class: 'caret', text: collapsed ? '▶' : '▼' }),
    h('h2', { text: 'Уведомления без карточки' }),
    h('span', { class: 'count', text: String(notes.length) }),
    h('span', { class: 'hint', text: 'обычно уже закрытые PR и issue' })
  ]);
  var children = [head];
  if (!collapsed) {
    var cards = h('div', { class: 'cards' });
    notes.forEach(function (n) {
      cards.appendChild(h('article', { class: 'card idle' }, [
        h('div', { class: 'kind', text: n.kind === 'Issue' ? '🐛' : n.kind === 'PullRequest' ? '🔀' : '🔔' }),
        h('div', { class: 'main' }, [
          h('div', { class: 'where' }, [
            h('span', { class: 'repo', text: n.repo }),
            n.number ? h('span', { class: 'num', text: '#' + n.number }) : null,
            h('span', { class: 'label', text: n.reason })
          ]),
          h('div', { class: 'title', text: n.title }),
          h('div', { class: 'row-actions' }, [
            n.number ? h('a', { class: 'btn btn-small', target: '_blank', rel: 'noopener',
              href: 'https://github.com/' + n.repo + '/issues/' + n.number,
              text: 'открыть' }) : null,
            h('button', { class: 'btn btn-small', text: 'прочитано', onclick: function () {
              act('/api/read', { thread: n.id }, 'отмечено прочитанным');
            } })
          ])
        ]),
        h('div', { class: 'side' }, [h('span', { text: ago(n.updated) })])
      ]));
    });
    children.push(cards);
  }
  return h('section', { class: 'group idle' }, children);
}

function renderSummary() {
  var el = document.getElementById('summary');
  if (!state || !state.generated) { el.textContent = 'первый опрос…'; return; }
  var s = state.summary || {};
  var bits = [];
  bits.push(s.you + ' ' + plural(s.you, 'ждёт', 'ждут', 'ждут') + ' тебя');
  bits.push(s.them + ' за другими');
  if (s.stale) bits.push(s.stale + ' залежалось');
  if (s.hidden) bits.push(s.hidden + ' скрыто');
  el.textContent = bits.join(' · ') + ' · опрошено ' + ago(state.generated);
}
