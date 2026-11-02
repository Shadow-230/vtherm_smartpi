# What a test report is

The definition every report on this branch follows. A report describes one **check**: one commit of
the fork's `qas`, run on the owner's test bench (test Home Assistant instances, never a production
one), with the QAS pack of that commit (`tests/qas/`).

## Layout of the branch

- `docs/test-reports/README.md`: the index of test cycles.
- `docs/test-reports/REPORT.md`: this definition.
- `docs/test-reports/<cycle>/`: one folder per test cycle, with its own `README.md` listing every
  checked version of `qas` in it. A cycle is one feature line on one base, e.g.
  `boiler-learning-pause` (the learning pause below Versatile Thermostat's central boiler
  threshold, on the author's releases).
- `docs/test-reports/<cycle>/qas-pr<NNN>-<commit>/`: one folder per checked version. `NNN` is the
  pull request that set `qas` to that version (three digits, so the folders sort), `commit` its merge
  commit (short). When a check also ran a later commit that changed only the test pack, that commit
  is named in the report, not in the folder.
- `<YYYY-MM-DD>-check.md`: the report, dated by the day the check ended. A check that runs in phases
  may publish one report per phase (`<date>-phase-<X>.md`) and a final one (`<date>-final.md`).

## What a check contains

In this order, each step only after the previous one was checked (no automatic chaining):
1. the staged commit confirmed, and the author's release named when it is the reference;
2. the local checks of the pack (`configuration.py --check`, `learned_state.py --check`,
   `pytest tests/`, `SMARTPI_QAS_LOCAL=1 pytest tests/test_qas_instance.py`, `run.py --list`);
3. the catalogue on freshly prepared instances, one per role;
4. repetitions of the new or changed scenarios;
5. reference runs against the author's release, when in scope.

## Sections of a report

1. **Result**: the verdicts and the few numbers that matter, first.
2. **What was tested**: commits of SmartPI (and the author's release it is built on), the pack's
   commit, Versatile Thermostat (version and commit), Home Assistant; what changed since the
   previous check.
3. **Bench changes**: scripts and procedures changed for the check.
4. **Local checks**: a table of the commands and their results.
5. **The catalogue, the repetitions, the reference runs**: one section each, with PASS counts,
   durations against the catalogue's typical time (ratio; 0.5–1.5 is plausible, outside it is
   explained), and the measurements the scenarios record.
6. **Home Assistant's log**: ERROR/CRITICAL (none expected) and every WARNING kind with its
   instances, count and triage.
7. **Where the findings come from**: our change, the author's code (the same in the author's
   release), Versatile Thermostat, the environment, or the test pack.
8. **Gaps for the coding session**: what the pack lacks or measures wrongly.
9. **The owner's decisions**: what the numbers leave to the owner.
10. **State of the instances** at the end.

## Rules

- A verdict is PASS, FAIL or NOT RUN; a scenario that "passes on the measurement" must show that
  the measurement took place.
- Every FAIL, every duration outside 0.5–1.5× and every unexpected log line is triaged with
  evidence before the report is written.
- Every number cites its source: the scenario's result, the runner's log, Home Assistant's log or
  history. The evidence itself stays on the bench; reports contain only markdown.
- Before publication, an independent read-only reviewer checks every verdict, number and claim
  against the raw evidence; each finding is fixed or rejected with a reason, and logged.
- A report is published only with the owner's yes, after a check for secrets (the fork is public).
- Reports are in English. This branch shares no history with the code and is never merged into a
  code branch or offered to the author.
