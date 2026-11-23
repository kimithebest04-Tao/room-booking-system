# Documented bug fix: a failing CI run, then a passing one

Section 4.4 of the brief requires a failure that an existing test detects, the
corresponding fix and both CI runs. The fault was introduced on a **branch that
is not connected to the deployment**, so the deployed application was never
broken: Render deploys `main` only, and this branch never reached `main` while
the fault existed.

## 1. The fault

Branch: `bugfix/overlap-boundary`

One comparison in `backend/processing.py` (`intervals_overlap`) was changed from
half-open to closed intervals:

```diff
-    return start_a < end_b and start_b < end_a
+    return start_a <= end_b and start_b <= end_a
```

This is a realistic slip (the boundary is exactly where `<` matters) and
`tests/test_domain_rule.py::test_overlap_rule_matches_the_hand_computed_table`
already covers it: the case `09:00-10:00` vs `10:00-11:00` expects *no*
conflict. No test was added for the demonstration, so the failure came from the
existing suite.

## 2. The failing run

```bash
git switch -c bugfix/overlap-boundary
# apply the change above
git commit -am "wip: try closed interval comparison"   # 9a11ea7
git push -u origin bugfix/overlap-boundary
```

CI run link (failing):
<https://github.com/kimithebest04-Tao/room-booking-system/actions/runs/45179834458>

Commit `9a11ea7`, workflow `CI`, job `test`, conclusion **failure** (the job
failed at the "Run the shared test suite" step, not at setup or linting).

Failure observed (the same two parametrised cases fail when the branch is run
on the development machine, `python -m pytest`):

```
FAILED tests/test_domain_rule.py::test_overlap_rule_matches_the_hand_computed_table[09:00-10:00-10:00-11:00-False]
FAILED tests/test_domain_rule.py::test_overlap_rule_matches_the_hand_computed_table[09:00-10:00-08:00-09:00-False]
2 failed, 29 passed, 1 warning
```

Symptom: back-to-back reservations were reported as conflicting, so two
consecutive meetings in the same room could no longer be booked, and the
identical slot could be reported as taken by a meeting that had already ended.

Cause: `<=` makes the two intervals share the single boundary instant, while the
domain model treats a reservation as the half-open interval `[start, end)`. A
meeting that ends at 10:00 does not occupy 10:00.

## 3. The fix

```diff
-    return start_a <= end_b and start_b <= end_a
+    return start_a < end_b and start_b < end_a
```

The test was **not** changed, skipped or marked xfail: the failing assertion
describes the intended behaviour, the implementation was wrong.

```bash
git commit -am "fix: restore the half-open comparison so adjacent slots stay free"   # ffbc514
git push
```

CI run link (passing):
<https://github.com/kimithebest04-Tao/room-booking-system/actions/runs/45180126629>

Commit `ffbc514`, same branch, same workflow and job, conclusion **success**.

Result:

```
31 passed, 1 warning in 8.23s
```

Verification beyond CI: the same behaviour was re-checked through the running
application on Render (`POST /bookings` with 09:00-10:00 then 10:00-11:00
answers 201, while 09:30-10:30 answers 409, and the stored rows keep their
original ids).

## 4. Checklist

- [x] the failing run is visible and shows a real test failure, not a lint or
      setup problem
      (<https://github.com/kimithebest04-Tao/room-booking-system/actions/runs/45179834458>)
- [x] the fix commit contains no test modifications
      (`git show --stat ffbc514` touches `backend/processing.py` only)
- [x] the passing run is on the same branch after the fix
      (<https://github.com/kimithebest04-Tao/room-booking-system/actions/runs/45180126629>)
- [x] the buggy commit was never deployed (the branch is not connected to Render
      or Streamlit Cloud; both follow `main`, which stayed green at
      <https://github.com/kimithebest04-Tao/room-booking-system/actions/runs/45179543399>)
