# QAS test pack — SmartPI on a real Home Assistant

The scenarios, the roles, the timing rules and the catalogue are in
[documentation/en/testing.md, section 6](../../documentation/en/testing.md#6-qas-on-a-real-home-assistant).
This file only says how the bench's testing session runs the pack, from the bench's directory, on
one commit of `qas` staged at `stage/vtherm_smartpi` and Versatile Thermostat's release tag staged
at `stage/versatile_thermostat`. Which instance runs which role, and when, is the testing session's
choice: a role names no instance.

1. Deploy instance N with the role it will run (`python stage/vtherm_smartpi/tests/qas/run.py
   --list` shows the scenarios and their roles):

       bin/deploy.sh --instance N --stage stage/vtherm_smartpi \
           --components stage/vtherm_smartpi/tests/qas/custom_components \
           --components stage/versatile_thermostat/custom_components \
           --config stage/vtherm_smartpi/tests/qas/config/<role>.yaml

2. Set it up as that role through the API, then it restarts:

       HA_INSTANCE=N python stage/vtherm_smartpi/tests/qas/setup/setup_role.py --role ROLE

3. Run the role's minimum set, then its learned set, or named scenarios (the long ones detached):

       HA_INSTANCE=N python stage/vtherm_smartpi/tests/qas/run.py --role ROLE M
       HA_INSTANCE=N python stage/vtherm_smartpi/tests/qas/run.py --role ROLE L
       HA_INSTANCE=N python stage/vtherm_smartpi/tests/qas/run.py --role neighbours QD1 QD4

   `M` runs from a fresh setup, where SmartPI still learns. `L` starts with QL0, which loads the
   learned models. A scenario whose SmartPI phase is not met (`--list` shows the phase each one
   needs, and what it does to the learned state) is NOT RUN, with the reason, in the results file.

4. Compare the pairs once both instances have run, naming the two instances (or their results
   files): `python stage/vtherm_smartpi/tests/qas/compare.py qb1 NEW REF` (NEW ran `base`, REF
   `ref-0.4.0`) and `… compare.py qe5 NEW REF` (NEW ran `zone`, REF `zone-ref`).

The runner finds the bench's `lib/` in `TESTBED_ROOT` or the current directory. Nothing here is
named `test_*.py`: the fork's CI does not collect it. After a change to `roles.py`, run
`python tests/qas/config/build.py` (CI checks the files are up to date), and, after a change to a
room the learned states come from, `python tests/qas/build_fixtures.py` (CI checks each file holds
a learned model of its room).
