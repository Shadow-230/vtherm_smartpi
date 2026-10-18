# Test reports of the fork's `qas`

This branch holds only test reports: the QAS runs of the fork's `qas` on a real Home Assistant
(the owner's test bench). It shares no history with the code and is never merged into `dev`,
`qas` or `master`, so a report never changes a code branch.

The reports are in [`docs/test-reports/`](docs/test-reports/README.md): one folder per tested
version, named `qas-pr<NNN>-<commit>` — the pull request that set `qas` to that version (three
digits, so the folders sort in order) and its merge commit.
