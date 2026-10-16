#!/bin/bash
# Usage: S=<scratch dir with venv/ and vt/> run_suite.sh <checkout> <label>
# CI scope, then the full local scope with branch coverage (ANALIZA.md §3).
cd "$1" || exit 1
export PYTHONPATH=$S/vt:$PWD
$S/venv/bin/python -m pytest tests -q -p no:cacheprovider --ignore=tests/qas -n 4 > $S/$2_ci.txt 2>&1
COVERAGE_FILE=$S/.coverage_$2 SMARTPI_SIM_LOCAL=1 SMARTPI_QAS_LOCAL=1 $S/venv/bin/python -m pytest tests -q -p no:cacheprovider -n 4 \
  --cov=custom_components/vtherm_smartpi --cov-branch --cov-report=json:$S/cov_$2.json > $S/$2_local.txt 2>&1
