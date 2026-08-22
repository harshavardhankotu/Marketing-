# LOOP RULES — non-negotiable for autonomous work in this repo

These rules bind any autonomous/loop session. They exist because this project
touches real revenue attribution and legal-compliance surfaces where
subtly-wrong code is expensive even when tests pass.

## 1. Secrets — never read, never echo

- NEVER open, read, print, diff, or transmit `.env`.
- Never print values from `data/campaigns.db` user/session tables.
- If a task seems to require credential values: STOP and report instead.

## 2. Write-scope — allowed vs forbidden paths

Autonomous tasks may ONLY modify files explicitly listed in the active
`loop-prompt.md` "Scope" block. In addition, these are forbidden to ANY loop,
always:

| Path | Why off-limits |
|---|---|
| `bots/config.py` | secrets + integration switches |
| `app.py` auth/postback/settings routes | money & identity surfaces (HMAC, sessions) |
| `bots/idempotency.py`, `bots/db_manager.py` schema block | financial correctness core |
| `templates/legal_*.html`, `COMPLIANCE.md` | legal claims need human sign-off |
| `.env*`, `data/**`, `deploy/**`, `.github/**` | infra/secrets |

Safe-by-default zones for loops: `generators/**` (pure renderers),
`bots/deal_scorer.py`-style pure-logic modules, `static/css/**`,
`tests/**` (new files), docs.

## 3. Verification gate — "complete" means evidence

A task is NOT complete until, in order:

```bash
python -m py_compile <every touched .py file>
python tests/test_<task_name>.py        # task's own new suite exits 0
python tests/full_feature_audit.py      # 249-check audit still green (exit 0)
```

Paste the tail of each run as evidence in the final summary. Claiming success
without pasted output = failure.

## 4. Branching

- All loop work happens on disposable branches: `git checkout -b loop/<name>`.
- NEVER commit directly to `main`; never push loop branches without asking.
- Human reviews, cherry-picks or merges. `main` stays always-green.

## 5. Stop conditions

- All gates green → summarize with evidence, stop.
- Blocked / ambiguous / would-touch-a-forbidden-path → STOP, report the exact
  blocker and the minimal human decision needed. Do not guess around it.
