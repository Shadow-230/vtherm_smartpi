# Test reports

One folder per tested version of `qas`: `qas-pr<NNN>-<commit>`, where `NNN` is the pull request
that set `qas` to that version (zero-padded to three digits) and `commit` its merge commit. Each
report's header names the exact commits and the environment it ran on.

| Version (`qas`) | Tested (UTC) | Versatile Thermostat | Home Assistant | Smart Boiler | Reports | Result so far |
|---|---|---|---|---|---|---|
| [PR #32](https://github.com/Shadow-230/vtherm_smartpi/pull/32), [7568df0](https://github.com/Shadow-230/vtherm_smartpi/commit/7568df09a22bf31c7357628c9e4149b82263b802) | 2026-10-17 – (running) | 10.5.1 (17ea26e) | 2026.9.3 | absent | [phase A](qas-pr032-7568df0/2026-10-18-phase-A.md), [phase C](qas-pr032-7568df0/2026-10-21-phase-C.md) | phase A: no SmartPI error; no FAIL traced to SmartPI's behaviour; findings F1 (diagnostics lag a water-source change) and F2 (emitter_cooling not published within the pack's wait; most likely F1); 12 test-pack defects. Phase C (the minimum set on the learned state): 23 of 28 passed, no SmartPI error; QW6 and QW1s pass on the learned zone; F1 extended to door connections and source roles; F9 minor (AutoCalib after a learning reset); test-pack defects P15–P17. Phase B running |
