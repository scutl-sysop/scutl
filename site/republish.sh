#!/bin/bash
# Site publish heartbeat (cst-j01t): full regenerate + deploy. Runs as
# a bell job every 10 min so the page serial is a live claim that the
# publishing pipeline works — not a fossil of the last human deploy.
set -euo pipefail
V=/home/star/seats/star/work/scutl/.venv/bin/python
S=/home/star/seats/star/work/scutl/site
$V $S/generate.py --out $S/out >/dev/null
$V $S/status.py --out $S/out/status.html >/dev/null
$V $S/scoreboard.py --out $S/out/smutbench >/dev/null
cp -rf $S/out/. /var/www/scutl/
cp -rf $S/out/smutbench/. /var/www/smutbench/
cp -f $S/assets/smutbench-shrimp.svg /var/www/smutbench/shrimp.svg
