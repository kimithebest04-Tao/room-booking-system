# Documented bug fix: a failing CI run, then a passing one

Section 4.4 of the brief requires a failure that an existing test detects, the
corresponding fix and both CI runs. The fault is introduced on a **branch that
is not connected to the deployment**, so the deployed application is never
broken.

## 1. The fault

Branch: `bugfix/overlap-boundary`

Change one comparison in `backend/processing.py` (`intervals_overlap`) from
half-open to closed intervals:

```diff
-    return start_a < end_b and start_b < end_a
+    return start_a <= end_b and start_b <= end_a
```

This is a realistic slip (the boundary case is exactly where `<` matters) and
`tests/test_domain_rule.py::test_overlap_rule_matches_the_hand_computed_table`
already covers it: the case `09:00–10:00` vs `10:00–11:00` expects *no* conflict.

## 2. The failing run

```bash
git switch -c bugfix/overlap-boundary
# apply the change above
git commit -am "wip: try closed interval comparison"
git push -u origin bugfix/overlap-boundary
```

CI run link (failing): `<paste the GitHub Actions run URL>`

Failure observed:

```
<paste the pytest summary, for example:
FAILED tests/test_domain_rule.py::test_overlap_rule_matches_the_hand_computed_table[09:00-10:00-10:00-11:00-False]
E   assert True is False
1 failed, 26 passed>
```

Symptom: back-to-back reservations were reported as conflicting, so two
consecutive meetings in the same room could no longer be booked.

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
git commit -am "fix: restore half-open interval comparison for the boundary case"
git push
```

CI run link (passing): `<paste the GitHub Actions run URL>`

Result:

```
<paste: 27 passed>
```

Verification beyond CI: the same behaviour was re-checked through the running
application (`POST /bookings` with 09:00–10:00 then 10:00–11:00 answers 201,
while 09:30–10:30 answers 409).

## 4. Checklist

- [ ] the failing run is visible and shows a real test failure, not a lint or
      setup problem
- [ ] the fix commit contains no test modifications
- [ ] the passing run is on the same branch after the fix
- [ ] the buggy commit was never deployed (the branch is not connected to Render
      or Streamlit Cloud)
