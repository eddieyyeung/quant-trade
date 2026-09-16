## Tasks

### 1. Rewrite weeks_between with strict progress guarantee

- [x] Rewrite `weeks_between()` in `src/quant_trade/data/calendar.py` — gap-jump branch + next-Monday anchoring, removing `self._sorted.index(friday) + 1` IndexError hazard

### 2. Regression tests

- [x] Add `tests/test_calendar.py` with 5 tests: dense weekly pairs, Golden Week gap termination, start-in-gap skip, no duplicate weeks, end-without-exec-day

### 3. Verify against real data

- [x] Reproduce old stall on real DB copy (20001+ iterations, current pinned at 2023-10-03) before fix; new algorithm returns 163 weeks in 2.3ms
- [x] End-to-end: `create_session` via API completes in 26-53ms with real calendar (was hanging forever)
