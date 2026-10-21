# Test reports

One folder per tested version of `qas`: `qas-pr<NNN>-<commit>`, where `NNN` is the pull request
that set `qas` to that version (zero-padded to three digits) and `commit` its merge commit. Each
report's header names the exact commits and the environment it ran on.

| Version (`qas`) | Tested (UTC) | Versatile Thermostat | Home Assistant | Smart Boiler | Reports | Result |
|---|---|---|---|---|---|---|
| [PR #32](https://github.com/Shadow-230/vtherm_smartpi/pull/32), [7568df0](https://github.com/Shadow-230/vtherm_smartpi/commit/7568df09a22bf31c7357628c9e4149b82263b802) | 2026-10-17 – 2026-10-21 | 10.5.1 (17ea26e) | 2026.9.3 | absent | **[final](qas-pr032-7568df0/2026-10-21-final.md)**, [phase A](qas-pr032-7568df0/2026-10-18-phase-A.md), [phase B](qas-pr032-7568df0/2026-10-21-phase-B.md), [phase C](qas-pr032-7568df0/2026-10-21-phase-C.md) | No SmartPI error in about 100 runs. Holds: a plain room learned within 1.5 % and the same model as 0.4.0; the upgrade from 0.4.0 and the way back; the zone with two sources; water, boiler call, providers, repairs. Fails: F7 (a door neighbour blocked by an always-open one) and F8 (a moving always-open neighbour biases a and b), confirmed; F1 (diagnostics not written on dependency changes). Candidates F4, F5; F9 minor; F6 withdrawn; F3 re-triaged as the pack's simulator. Test pack: P1–P17. Work list in the final report §10 |
