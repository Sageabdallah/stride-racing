#!/usr/bin/env bash
# Shared by 06_schedules.sh and 07b_fargate_schedules.sh.
#
# update-schedule is FULL-REPLACEMENT: any parameter omitted from the call
# reverts to its default, and State defaults to ENABLED. Both scripts
# omitted --state, so every deploy silently undid an operator's "disable
# this one while we investigate" (EXECUTION_STATUS.md, 2026-08-02).
# EventBridge Scheduler has no enable/disable API — update-schedule is the
# only writer — so preserving state has to happen inside the writer.

# Current state of a schedule: ENABLED, DISABLED, or ABSENT.
#
# Reading "no answer" must never be treated as ENABLED. That is precisely
# how a deliberately disabled schedule gets switched back on by a deploy
# that never knew it was off, which is the defect this exists to close.
# Throttling and 5xx mean "ask again" and are retried; AccessDenied and an
# expired token mean "cannot know" and fail loudly. The callers already
# retry writes 10x/10s for IAM propagation, so a read with no retries at
# all would invent a new deploy failure mode.
sched_current_state() {
  local NAME=$1
  local ERR="${TMPDIR:-/tmp}/stride_sched_state_err.$$"
  local OUT RC attempt
  for attempt in 1 2 3 4 5; do
    RC=0
    OUT=$(aws scheduler get-schedule --name "$NAME" \
      --query State --output text 2>"$ERR") || RC=$?
    if [ "$RC" = 0 ]; then
      case "$OUT" in
        ENABLED|DISABLED)
          printf '%s\n' "$OUT"
          rm -f "$ERR"
          return 0
          ;;
      esac
      echo "FATAL $NAME: State read back as '$OUT', not ENABLED/DISABLED" >&2
      rm -f "$ERR"
      return 1
    fi
    if grep -q ResourceNotFoundException "$ERR"; then
      printf 'ABSENT\n'
      rm -f "$ERR"
      return 0
    fi
    if ! grep -qE "Throttling|TooManyRequests|RequestTimeout|ServiceUnavailable|InternalError|InternalServerError" "$ERR"; then
      break
    fi
    sleep $((attempt * 2))
  done
  echo "FATAL $NAME: cannot read current schedule state — refusing to guess," >&2
  echo "       because guessing ENABLED is how a disable gets undone." >&2
  cat "$ERR" >&2
  rm -f "$ERR"
  return 1
}

# The --state argument to pass when UPDATING an existing schedule, echoed
# to stdout. A schedule that does not exist yet is created ENABLED, which
# is correct: there is no operator intent to preserve.
#
# A preserved DISABLED is announced loudly. A schedule silently staying off
# is as bad as one silently coming back on: both are the deploy telling you
# something it does not actually know.
sched_state_to_keep() {
  local NAME=$1
  local STATE
  STATE=$(sched_current_state "$NAME") || return 1
  if [ "$STATE" = "DISABLED" ]; then
    echo "  !! $NAME is DISABLED — preserving that; it will NOT fire" >&2
  fi
  if [ "$STATE" = "ABSENT" ]; then
    STATE=ENABLED
  fi
  printf '%s\n' "$STATE"
}
