# Test reports

One folder per tested version of `qas`: `qas-pr<NNN>-<commit>`, where `NNN` is the pull request
that set `qas` to that version (zero-padded to three digits) and `commit` its merge commit. Each
report's header names the exact commits and the environment it ran on.

| Version (`qas`) | Tested (UTC) | Versatile Thermostat | Home Assistant | Smart Boiler | Reports | Result so far |
|---|---|---|---|---|---|---|
| [PR #32](https://github.com/Shadow-230/vtherm_smartpi/pull/32), [7568df0](https://github.com/Shadow-230/vtherm_smartpi/commit/7568df09a22bf31c7357628c9e4149b82263b802) | 2026-10-17 – (running) | 10.5.1 (17ea26e) | 2026.9.3 | absent | [phase A](qas-pr032-7568df0/2026-10-18-phase-A.md) | phase A: no SmartPI error; no FAIL traced to SmartPI's behaviour; findings F1 (diagnostics lag a water-source change) and F2 (emitter_cooling timing, to place); 12 test-pack defects. Phase B running |
