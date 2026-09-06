#!/bin/bash
# Site publish heartbeat (cst-j01t): full regenerate + deploy. Runs as
# a bell job every 10 min so the page serial is a live claim that the
# publishing pipeline works — not a fossil of the last human deploy.
set -euo pipefail
V=/home/star/seats/star/work/scutl/.venv/bin/python
S=/home/star/seats/star/work/scutl/site
$V $S/generate.py --out $S/out >/dev/null
# Sign SHA-256SUMS (cst-j01t launch checklist): ssh signature under the
# dedicated scutl-release key; verifier ships beside it. Verify with:
#   ssh-keygen -Y verify -f allowed_signers -I scutl-release -n file \
#     -s SHA-256SUMS.sig < SHA-256SUMS
ssh-keygen -Y sign -f /home/star/.ssh/scutl-release -n file -q $S/out/SHA-256SUMS
echo "scutl-release $(cut -d' ' -f1-2 /home/star/.ssh/scutl-release.pub)" > $S/out/allowed_signers
$V $S/status.py --out $S/out/status.html >/dev/null
$V $S/scoreboard.py --out $S/out/scutbench >/dev/null
cp -rf $S/out/. /var/www/scutl/
cp -rf $S/out/scutbench/. /var/www/scutbench/
cp -f $S/assets/scutbench-shrimp.svg /var/www/scutbench/shrimp.svg
