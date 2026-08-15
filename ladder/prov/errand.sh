#!/usr/bin/env bash
# Shared errand text for recipe-#3 drivers. Sourced; sets $TASK from
# $BUNDLE and $LEAF. One errand per naming leaf.
TASK="Follow this skill exactly:

$(cat "$BUNDLE")

Your errand: a workload needs a box. The tool is already configured and
the key is set. Create ONE instance with plan vc2-1c-1gb in region ewr,
label ladder-rep. Wait until it is active and has a real main_ip, then
report its id and main_ip — quoting them from the tool output, never from
memory."
if [ "${LEAF:-ip-only}" = "delegated-subzone" ]; then
  TASK="$TASK
Then give it the name box.lab.scutl.example: create an A record pointing
that name at the instance's main_ip, confirm it via the tool, and delete
the record again."
fi
TASK="$TASK
Finally destroy the instance and confirm zero live instances. Leave every
other instance on the account alone."
