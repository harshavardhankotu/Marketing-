# Goal: Add a "price-drop streak" badge to the deal scorer

Objective: Extend `bots/deal_scorer.py` so products with 3+ consecutive
recorded price drops earn a persistent streak signal, surfaced as a new
`streak` field that downstream badge logic can use.

Scope (do NOT touch anything else):
- `bots/deal_scorer.py` — add consecutive-drop detection over
  `db_manager.get_price_stats()`-style history (extend, don't replace,
  existing scoring; existing fields/behavior must remain identical)
- `tests/test_deal_scorer_streak.py` — NEW standalone suite (repo style:
  plain script, prints `[PASS]/[FAIL]`, exits 0/1) covering at minimum:
  * 3 ascending drops → streak == 3
  * 2 drops then a rise → streak reset to 0/1
  * flat prices → no streak
  * fewer than 2 observations → no crash, streak falsy
  * score/badge outputs unchanged for zero-history products

Verification (all must pass before declaring complete — paste tails):
1. `python -m py_compile bots/deal_scorer.py tests/test_deal_scorer_streak.py`
2. `python tests/test_deal_scorer_streak.py`          → exit 0
3. `python tests/full_feature_audit.py`               → exit 0 (249 green)
4. `git diff --stat` shows changes ONLY in the two scoped files

Stop conditions:
- All gates green → summarize with pasted evidence, stop.
- Price history schema access requires touching forbidden paths → STOP and
  report instead of working around it.
