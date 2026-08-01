# github-zoo

One board for every open pull request, issue and discussion that involves you —
sorted by the only question that matters: **whose move is it?**

[По-русски](README.ru.md)

GitHub can already list what is open. That list is not useful, because
everything on it looks equally alive. A pull request that has waited three
weeks for a reviewer sits next to one with a question you never answered, and
both are sorted by a timestamp that moves whenever a bot touches them.

github-zoo sorts by obligation instead:

| bucket | meaning |
| --- | --- |
| **your move** | nothing happens until you act — an unanswered review comment, a red build on your branch, a review someone asked you for, an issue on your repository nobody replied to |
| **waiting on them** | your part is done; only age is worth watching |
| **done** | merged or closed in the last `done_days` — what actually shipped |
| **quiet** | drafts and announcements nobody is waiting on |
| **older than a month** | no movement in `ignore_days` — real, but not today's problem, and folded away |

That last bucket is why the board stays readable. Without it the top of the
list was a 2019 review request that is technically still pending. Nothing is
thrown away — the count is always visible and one click opens it.

Projects are tabs, newest activity first, each showing how many items there are
waiting on you.

## What it watches

* **Pull requests** you wrote, reviewed, commented on, were asked to review, or
  were assigned — anywhere on GitHub, not just in your own repositories.
* **Review threads inside them.** This is where work actually gets forgotten,
  so it is the strongest signal on the board: an unresolved thread where
  someone else spoke last is your move. Threads whose lines were rewritten by a
  later push are ignored — they cannot be answered any more.
* **CI status** per pull request, so a red build on your own branch shows up as
  work rather than as a notification you scrolled past.
* **Issues**, including ones opened on your repositories that you have not
  answered — the classic thing that quietly ages out.
* **Discussions**, both yours and ones you wrote in elsewhere — including the
  replies nested under each comment, which is where the answers actually are.
  A thread here had 7 comments and 59 replies, so reading only the top level
  reported a message from weeks earlier and called it quiet. If you wrote in a
  discussion and someone answered after you, that is your move.
* **What you finished.** An open-only board cannot answer "what did I get
  done": three pull requests merged on Thursday simply vanish from it. Merged,
  closed and "not planned" are three different endings and say three different
  things. This is also where most homeless notifications went — a notice about
  a merged pull request now has a card to attach to.
* **Your notification inbox**, minus the noise. `ci_activity` is dropped by
  default: on a busy upstream it is 200 of 280 notifications. A notification
  with no matching item — usually a merged pull request — gets its own section
  instead of disappearing.

The tab icon is the part of this you see most: blue with a tick when
nothing is waiting on you, amber with the count when something is. The count
is in the page title too, for a narrowed tab strip.

Anything can be **snoozed** (hidden until a chosen time) or **muted** (hidden
until it genuinely changes). A snooze is cancelled the moment the item moves,
so "not now" never becomes "missed it".

## Telegram

Optional. When something lands in your court, a message goes out; once a day a
digest lists what is still waiting. Delivery goes through
[telegram.sh](https://github.com/fabianonline/telegram.sh), which already holds
the proxy settings and an offline queue on hosts that need them.

## Install

On any Linux host with `python3` and systemd:

```bash
git clone https://github.com/anton-vinogradov/github-zoo.git
cd github-zoo
sudo ./install.sh
```

Then create a token with **repo**, **read:org** and **notifications** scopes at
<https://github.com/settings/tokens>, and store it encrypted:

```bash
printf %s 'ghp_your_token' | sudo systemd-creds encrypt --name=github-token - \
    /etc/github-zoo.d/github-token.cred
sudo ./install.sh
```

`systemd-creds` binds the ciphertext to that machine (to the TPM where there is
one) and hands the plaintext to the service in a tmpfs only it can read.
Nothing readable sits on disk. A token in the config file, by contrast, is a
token in every backup.

Edit `/etc/github-zoo.json` and restart. The dashboard is on port 8817.

Running it on a laptop needs none of that: with no token configured it borrows
the one `gh` is already using.

## Updating

```bash
GITHUB_ZOO_HOST=user@host ./deploy.sh
```

Runs the tests, copies the tree, records the deployed commit in `VERSION` —
shown in the dashboard footer, so prod and repository can never quietly
disagree — restarts the service and checks the API answers.

## Cost

GraphQL bills by node count against 5000 points an hour. A full poll of ~30
pull requests costs about 60, but the expensive half (threads and CI) is
fetched only for items whose `updatedAt` moved, so a steady-state poll costs
about 5. The notification inbox is fetched conditionally and answers `304` —
free — when nothing changed. At the default 300-second interval this uses
roughly 1% of the budget.

## Configuration

Thresholds are on the **settings page** — `/settings`, linked from the header.
Changing one recomputes the board immediately, because a threshold is a
question about the cards already collected rather than about GitHub. Bounds are
enforced on the server; a page cannot be trusted to be the thing enforcing
them. Overrides live in their own file and never rewrite the config, so
"Сбросить" always gets you back.

Everything the page must not touch — the token, the Telegram chat, the paths —
stays in `/etc/github-zoo.json`, created from `collector/config.example.json`,
which documents every field inline. The ones worth knowing:

| field | default | meaning |
| --- | --- | --- |
| `poll_seconds` | 300 | how often to ask GitHub |
| `stale_days` | 14 | how long your move may wait before it is called stale |
| `ignore_days` | 30 | untouched for this long → out of the way (shown on request) |
| `done_days` | 7 | how long merged and closed work stays on the board; 0 turns it off |
| `unreviewed_days` | 3 | your pull request with no reviewer this long → ask for one |
| `extra_searches` | `[]` | extra GitHub search strings to include |
| `notifications_include_ci` | `false` | let CI notifications back in |

## Layout

```
collector/   hub.py (HTTP + poll loop), collect.py (queries), rules.py (whose
             move), store.py (history), acks.py (snooze), alerts.py (Telegram)
ui/          core.js, cards.js, details.js, wiring.js
tests/       what the rules must mean
```

Standard library only — no venv on the host, no build step for the page.

## Licence

MIT.
