#!/usr/bin/env bash
# capture-transcript.sh — shared transcript capture for ladder hermes drivers.
#
#   capture_transcript <rep_workdir> <stamp_file>
#
# Writes <rep>/transcript.txt (the FULL agent session: every tool call and
# output — what the anti-fabrication and key-hygiene checks grade) and
# <rep>/transcript-source.txt (provenance, read by grade.py).
#
# Hermes ≤0.19 wrote one file per session under ~/.hermes/sessions/.
# Hermes 0.20.0 moved the session store into ~/.hermes/state.db (SQLite);
# the sessions dir stays empty and the old "newest file" scan silently
# captured nothing (cst-3ng). Order of preference:
#   1. a sessions/ file written during this rep        (old hermes)
#   2. `hermes sessions export` of the session started during this rep
#   3. final.txt — LOUD fallback: only the last message gets graded
#
# Only a session started/written DURING this rep may serve as its
# transcript (rung-ref-pub rep-01 lesson): both paths gate on stamp_file's
# mtime, taken before the agent launched.

capture_transcript() {
  local rep="$1" stamp="$2"
  local hermes_bin="${HERMES_BIN:-hermes}"
  local sessions="$HOME/.hermes/sessions"
  local statedb="$HOME/.hermes/state.db"

  rm -f "$rep/transcript.txt" "$rep/transcript-source.txt"

  # 1. legacy per-session files
  if [ -d "$sessions" ]; then
    local latest
    latest=$(find "$sessions" -type f -newer "$stamp" -printf '%T@ %p\n' \
      2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
    if [ -n "$latest" ]; then
      cat "$latest" > "$rep/transcript.txt" 2>/dev/null || true
      [ -s "$rep/transcript.txt" ] && \
        echo "sessions-file:$(basename "$latest")" > "$rep/transcript-source.txt"
    fi
  fi

  # 2. hermes 0.20+ SQLite session store
  if [ ! -s "$rep/transcript.txt" ] && [ -f "$statedb" ]; then
    local since sid
    since=$(stat -c %Y "$stamp")
    sid=$(python3 - "$statedb" "$since" <<'PYEOF'
import sqlite3, sys
db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
row = db.execute(
    "select id from sessions where started_at >= ? "
    "order by started_at desc limit 1", (float(sys.argv[2]),)).fetchone()
print(row[0] if row else "")
PYEOF
    ) || sid=""
    if [ -n "$sid" ]; then
      "$hermes_bin" sessions export --format jsonl --session-id "$sid" --yes - \
        > "$rep/transcript.txt" 2>/dev/null || true
      [ -s "$rep/transcript.txt" ] && \
        echo "state-db-export:$sid" > "$rep/transcript-source.txt"
    fi
  fi

  # 3. loud fallback — grading sees only the final message
  if [ ! -s "$rep/transcript.txt" ]; then
    cp -f "$rep/final.txt" "$rep/transcript.txt"
    echo "FALLBACK-final-only" > "$rep/transcript-source.txt"
    echo "==============================================================" >&2
    echo "WARNING: no full session captured for $rep — transcript.txt is" >&2
    echo "final.txt only. Full-session checks (anti-fabrication, key" >&2
    echo "hygiene) are grading the last message, not the session (cst-3ng)." >&2
    echo "==============================================================" >&2
  fi
}
