# QAS test pack — SmartPI on a real Home Assistant

The scenarios, the roles of the eight instances, the timing rules and the catalogue are in
[documentation/en/testing.md, section 6](../../documentation/en/testing.md#6-qas-on-a-real-home-assistant).
This file only says how the bench's testing session runs the pack, from the bench's directory, on
one commit of `qas` staged at `stage/vtherm_smartpi` and Versatile Thermostat's release tag staged
at `stage/versatile_thermostat`.

1. Deploy instance N with its role (`python stage/vtherm_smartpi/tests/qas/run.py --list` shows the
   roles; instance N's role is the one `roles.py` gives it):

       bin/deploy.sh --instance N --stage stage/vtherm_smartpi \
           --components stage/vtherm_smartpi/tests/qas/custom_components \
           --components stage/versatile_thermostat/custom_components \
           --config stage/vtherm_smartpi/tests/qas/config/<role>.yaml

2. Set it up through the API, then it restarts:

       HA_INSTANCE=N python stage/vtherm_smartpi/tests/qas/setup/setup_role.py

3. Run the minimum set, or named scenarios (the long ones detached):

       HA_INSTANCE=N python stage/vtherm_smartpi/tests/qas/run.py M
       HA_INSTANCE=N python stage/vtherm_smartpi/tests/qas/run.py QD1 QD4

4. Compare the pairs once both instances have run: `python stage/vtherm_smartpi/tests/qas/compare.py qb1`
   (instances 1 and 7) and `… compare.py qe5` (instances 3 and 4).

The runner finds the bench's `lib/` in `TESTBED_ROOT` or the current directory. Nothing here is
named `test_*.py`: the fork's CI does not collect it. After a change to `roles.py`, run
`python tests/qas/config/build.py` (CI checks the files are up to date).
