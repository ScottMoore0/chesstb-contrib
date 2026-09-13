#!/usr/bin/env bash
# Run every item's tests and print a single verdict table.
# Nothing here is asserted that is not measured.
cd "$(dirname "$0")" || exit 1
PY=${PY:-python}
PASS=0; FAIL=0; SKIP=0
declare -a RESULTS

run() {   # run <name> <command...>
  local name="$1"; shift
  local out rc
  out=$("$@" 2>&1); rc=$?
  if [ $rc -eq 0 ]; then RESULTS+=("PASS  $name"); PASS=$((PASS+1));
  else RESULTS+=("FAIL  $name  (rc=$rc)"); FAIL=$((FAIL+1)); fi
  printf '%s\n' "$out" | tail -6
  echo
}

skip() { RESULTS+=("SKIP  $1  -- $2"); SKIP=$((SKIP+1)); echo "SKIP $1: $2"; echo; }

echo "=============================================================="
echo " chesstb-contrib :: full test sweep"
echo "=============================================================="
echo

echo "--- item 07: reverse movegen (bijection) ---------------------"
# Full six-suite run takes ~10 min; it is the authoritative record and lives in
# item07-revmovegen/bijection_verified.txt. The sweep runs the quick subset.
run "item07 bijection (quick)" $PY -u item07-revmovegen/tests/test_bijection.py --quick

echo "--- item 02: reference generator (3-man + playout) -----------"
if [ -x item02-refverify/refgen2 ] || [ -x item02-refverify/refgen2.exe ]; then
  run "item02 refgen 3-man" ./item02-refverify/refgen2 KQvK KRvK
else
  skip "item02 refgen" "binary not built; run: g++ -O2 -std=c++17 -o refgen2 refgen.cpp"
fi

# Four-man DTM is where the capture defect lived, so three-man alone proves
# nothing about it. Needs the dumped four-man tables and 3-4 man Gaviota files.
if [ -f item02-refverify/tables/KQvKR.reftb ] && [ -n "$GAVIOTA" ]; then
  run "item02 4-man DTM vs Gaviota (sampled)" $PY item02-refverify/gaviota_dtm_diff.py \
      KQvKR KRvKR KQvKQ KBBvK --tables item02-refverify/tables --gaviota "$GAVIOTA" --sample 5000
else
  skip "item02 4-man DTM" "needs item02-refverify/tables/*.reftb for 4-man and GAVIOTA=<3-4 man Gaviota dir>"
fi

echo "--- item 01: dtm2pvs against reference tables ----------------"
if [ -f item02-refverify/tables/KQvK.reftb ]; then
  run "item01 dtm2pvs" $PY item01-dtm2pvs/dtm2pvs_chesstb.py \
      item01-dtm2pvs/tests/sample.epd --tb item02-refverify/tables --check-invariant
else
  skip "item01 dtm2pvs" "no reference tables; run refgen2 --dump tables"
fi

echo "--- item 04: client path handling ----------------------------"
run "item04 client" $PY item04-chesstb-client/tests/test_paths.py

echo "--- item 05: benchmark harness -------------------------------"
if [ -f item02-refverify/tables/KQvK.reftb ]; then
  run "item05 bench" $PY item05-benchmark/bench.py \
      --backend ref=item02-refverify/tables --probes 300
else
  skip "item05 bench" "no reference tables"
fi

echo "--- item 03: tablebase-draw pathfinder (self test) -----------"
if [ -f item02-refverify/tables/KQvK.reftb ]; then
  run "item03 pathfind" $PY item03-tbdrawpath/tbdrawpath.py \
      --tb item02-refverify/tables --self-test
else
  skip "item03 pathfind" "no reference tables"
fi

echo "--- item 06: PV trust metric (controls) ----------------------"
run "item06 pvtrust" $PY item06-pvtrust/pvtrust.py --self-test

echo "--- item 08: endgame training data ---------------------------"
if [ -f item02-refverify/tables/KQvK.reftb ]; then
  run "item08 nnue-data" $PY item08-nnue-data/gen_endgame_data.py \
      --tb item02-refverify/tables --configs KQvK,KRvK --per-config 200 \
      --out item08-nnue-data/out
else
  skip "item08 nnue-data" "no reference tables"
fi

echo "--- item 09: Stockfish 4-man DTM prototype -------------------"
if [ -x item09-sf-dtm4/dtm4 ] || [ -x item09-sf-dtm4/dtm4.exe ]; then
  run "item09 dtm4" ./item09-sf-dtm4/dtm4 KQvK KRvK
else
  skip "item09 dtm4" "binary not built; run: g++ -O2 -std=c++17 -o dtm4 dtm4.cpp"
fi

echo "=============================================================="
echo " VERDICT"
echo "=============================================================="
for r in "${RESULTS[@]}"; do echo "  $r"; done
echo
echo "  pass=$PASS  fail=$FAIL  skip=$SKIP"
[ $FAIL -eq 0 ]
