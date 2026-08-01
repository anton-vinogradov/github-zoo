/* github-zoo — the board: filter chips, groups and cards. */

'use strict';

function renderTabs() {
  var box = document.getElementById('tabs');
  box.textContent = '';
  if (!state || !state.items) return;

  var all = projects();
  if (!all.length) return;

  /* A project whose every card is past ignore_days gets no tab: those forks
     and one-off issues were eleven of eighteen tabs, all of them dead. The
     cards are still reachable — they are in "Старее месяца", which says how
     many there are. */
  var list = all.filter(function (p) { return p.fresh > 0; });
  var buried = all.length - list.length;
  if (view.repo && !list.some(function (p) { return p.repo === view.repo; })) {
    var pinned = all.filter(function (p) { return p.repo === view.repo; });
    list = list.concat(pinned);
  }

  var allWaiting = all.reduce(function (n, p) { return n + p.waiting; }, 0);
  var allTotal = all.reduce(function (n, p) { return n + p.total; }, 0);
  box.appendChild(tab('Все', '', allWaiting, allTotal, !view.repo, 0,
    buried ? buried + ' ' + plural(buried, 'проект целиком старее месяца',
                                   'проекта целиком старее месяца',
                                   'проектов целиком старее месяца') : '',
    function () { view.repo = ''; render(); }));

  var label = tabLabeller(list);
  list.forEach(function (p) {
    box.appendChild(tab(label(p.repo), p.repo, p.waiting, p.total,
      view.repo === p.repo, p.fresh, '', function () {
        /* Clicking the active tab goes back to everything — otherwise the only
           way out of a project is to find the "Все" tab again. */
        view.repo = view.repo === p.repo ? '' : p.repo;
        render();
      }));
  });
}

function tab(label, full, waiting, total, on, fresh, extra, onclick) {
  var parts = [];
  if (full) parts.push(full);
  if (waiting) parts.push(waiting + ' ' + plural(waiting, 'ждёт', 'ждут', 'ждут') + ' твоего ответа');
  parts.push(total + ' ' + plural(total, 'карточка', 'карточки', 'карточек'));
  if (fresh) parts.push('активность ' + ago(fresh));
  if (extra) parts.push(extra);
  return h('button', {
    class: 'tab' + (on ? ' on' : '') + (waiting ? ' has-waiting' : ''),
    onclick: onclick,
    title: parts.join(' · ')
  }, [
    h('span', { class: 'tab-name', text: label }),
    waiting ? h('span', { class: 'tab-n', text: String(waiting) }) : null
  ]);
}

/* Owner names eat the tab row and mostly repeat, so a tab shows just the
   repository name — until that is ambiguous. Four separate forks all called
   "ignite" made four identical tabs, so those get their owner back, shortened
   enough to still fit. */
function tabLabeller(list) {
  var seen = {};
  list.forEach(function (p) {
    var name = bareName(p.repo);
    seen[name] = (seen[name] || 0) + 1;
  });
  return function (repo) {
    var name = bareName(repo);
    if (seen[name] < 2) return name;
    var owner = repo.slice(0, repo.indexOf('/'));
    return (owner.length > 11 ? owner.slice(0, 10) + '…' : owner) + '/' + name;
  };
}

function bareName(repo) {
  var slash = repo.indexOf('/');
  return slash < 0 ? repo : repo.slice(slash + 1);
}

function renderFilters() {
  var box = document.getElementById('filters');
  box.textContent = '';
  if (!state || !state.items) return;

  var kindCounts = {};
  state.items.forEach(function (it) {
    if (it.hidden && !view.showHidden) return;
    if (view.repo && it.repo !== view.repo) return;
    kindCounts[it.kind] = (kindCounts[it.kind] || 0) + 1;
  });

  Object.keys(KIND_ICON).forEach(function (kind) {
    if (!kindCounts[kind]) return;
    box.appendChild(chip(KIND_ICON[kind] + ' ' + KIND_NAME[kind], kindCounts[kind],
      view.kinds[kind], function () {
        view.kinds[kind] = !view.kinds[kind];
        render();
      }));
  });
}

function chip(text, count, on, onclick) {
  return h('span', { class: 'chip' + (on ? ' on' : ''), onclick: onclick },
    [text, h('span', { class: 'n', text: String(count) })]);
}

function render() {
  renderTabs();
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
  // Folded-away items are counted out loud: a threshold that silently eats
  // seventeen cards is indistinguishable from a collector that lost them.
  if (s.archive) bits.push(s.archive + ' старее месяца');
  if (s.hidden) bits.push(s.hidden + ' скрыто');
  el.textContent = bits.join(' · ') + ' · опрошено ' + ago(state.generated);
}
