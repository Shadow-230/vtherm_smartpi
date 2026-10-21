# Test reports

One folder per tested version of `qas`: `qas-pr<NNN>-<commit>`, where `NNN` is the pull request
that set `qas` to that version (zero-padded to three digits) and `commit` its merge commit. Each
report's header names the exact commits and the environment it ran on.

| Version (`qas`) | Tested (UTC) | Versatile Thermostat | Home Assistant | Smart Boiler | Reports | Result so far |
|---|---|---|---|---|---|---|
| [PR #32](https://github.com/Shadow-230/vtherm_smartpi/pull/32), [7568df0](https://github.com/Shadow-230/vtherm_smartpi/commit/7568df09a22bf31c7357628c9e4149b82263b802) | 2026-10-17 – 2026-10-21 | 10.5.1 (17ea26e) | 2026.9.3 | absent | [phase A](qas-pr032-7568df0/2026-10-18-phase-A.md), [phase B](qas-pr032-7568df0/2026-10-21-phase-B.md), [phase C](qas-pr032-7568df0/2026-10-21-phase-C.md) | phase A: no SmartPI error; no FAIL traced to SmartPI's behaviour; findings F1 (diagnostics lag a water-source change) and F2 (emitter_cooling not published within the pack's wait; most likely F1); 12 test-pack defects. Phase C (the minimum set on the learned state): 23 of 28 passed, no SmartPI error; QW6 and QW1s pass on the learned zone; F1 extended to door connections and source roles; F9 minor (AutoCalib after a learning reset); test-pack defects P15–P17. Phase B (48–72 h): 8 of 12 runs passed, no SmartPI error; a plain room learned within 1.5 %; the upgrade from 0.4.0 and the way back keep the model; F7 (a door neighbour blocked by an always-open one) and F8 (a moving always-open neighbour biases a and b) confirmed; F4, F5 candidates; F3 re-triaged as the simulator (P15); P13, P14 |
