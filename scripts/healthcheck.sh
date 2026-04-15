#!/bin/bash
set -euo pipefail

cd /opt/the_blueprints
mkdir -p logs/scheduler

LOG_FILE="logs/scheduler/healthcheck.log"
TRACK_FILE="logs/scheduler/healthcheck.status"
STATE_FILE="logs/paper_positions_5usd.json"

# Optional local runtime env for alert credentials.
if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

MAX_STATE_AGE_SECONDS=${MAX_STATE_AGE_SECONDS:-2400}
ALERT_EMAIL=${ALERT_EMAIL:-}
ALERT_COOLDOWN_SECONDS=${ALERT_COOLDOWN_SECONDS:-1800}
NOTIFY_CHANNEL=${NOTIFY_CHANNEL:-telegram}
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-}
TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID:-}
TELEGRAM_THREAD_ID=${TELEGRAM_THREAD_ID:-}
TELEGRAM_API_BASE=${TELEGRAM_API_BASE:-https://api.telegram.org}

now_epoch=$(date +%s)
timestamp_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
host_name=$(hostname -f 2>/dev/null || hostname)

status="ok"
notes=()
state_age=-1

if ! systemctl is-active --quiet cron; then
  status="fail"
  notes+=("cron_inactive")
fi

if [ ! -x /opt/the_blueprints/scripts/run_cycle.sh ]; then
  status="fail"
  notes+=("run_cycle_missing_or_not_executable")
fi

if [ ! -f "$STATE_FILE" ]; then
  status="fail"
  notes+=("state_file_missing")
else
  state_mtime=$(stat -c %Y "$STATE_FILE")
  state_age=$((now_epoch - state_mtime))
  if [ "$state_age" -gt "$MAX_STATE_AGE_SECONDS" ]; then
    status="fail"
    notes+=("state_stale_${state_age}s")
  else
    notes+=("state_age_${state_age}s")
  fi
fi

if [ -f logs/cron.log ] && tail -n 200 logs/cron.log | grep -q "Traceback (most recent call last)"; then
  notes+=("traceback_seen_recently")
fi

previous_status=""
last_alert_epoch=0
if [ -f "$TRACK_FILE" ]; then
  # shellcheck disable=SC1090
  source "$TRACK_FILE" || true
  previous_status=${previous_status:-}
  last_alert_epoch=${last_alert_epoch:-0}
fi

send_telegram() {
  local text="$1"

  if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
    return 1
  fi

  if ! command -v curl >/dev/null 2>&1; then
    return 1
  fi

  local endpoint="${TELEGRAM_API_BASE}/bot${TELEGRAM_BOT_TOKEN}/sendMessage"
  if [ -n "$TELEGRAM_THREAD_ID" ]; then
    curl -fsS --max-time 10 -X POST "$endpoint" \
      --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
      --data-urlencode "message_thread_id=${TELEGRAM_THREAD_ID}" \
      --data-urlencode "text=${text}" \
      --data-urlencode "disable_web_page_preview=true" \
      >/dev/null
  else
    curl -fsS --max-time 10 -X POST "$endpoint" \
      --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
      --data-urlencode "text=${text}" \
      --data-urlencode "disable_web_page_preview=true" \
      >/dev/null
  fi
}

send_email() {
  local subject="$1"
  local body="$2"

  if [ -z "$ALERT_EMAIL" ]; then
    return 1
  fi

  if command -v sendmail >/dev/null 2>&1; then
    {
      echo "To: ${ALERT_EMAIL}"
      echo "From: blueprints-bot@${host_name}"
      echo "Subject: ${subject}"
      echo "Content-Type: text/plain; charset=UTF-8"
      echo
      echo "${body}"
    } | sendmail -t
    return $?
  fi

  if command -v mail >/dev/null 2>&1; then
    printf "%s\n" "$body" | mail -s "$subject" "$ALERT_EMAIL"
    return $?
  fi

  if command -v mailx >/dev/null 2>&1; then
    printf "%s\n" "$body" | mailx -s "$subject" "$ALERT_EMAIL"
    return $?
  fi

  return 1
}

send_notification() {
  local title="$1"
  local body="$2"
  local text="${title}

${body}"

  case "$NOTIFY_CHANNEL" in
    telegram)
      if send_telegram "$text"; then
        echo "telegram"
        return 0
      fi
      ;;
    email)
      if send_email "$title" "$body"; then
        echo "email"
        return 0
      fi
      ;;
    auto)
      if send_telegram "$text"; then
        echo "telegram"
        return 0
      fi
      if send_email "$title" "$body"; then
        echo "email"
        return 0
      fi
      ;;
    *)
      ;;
  esac

  return 1
}

send_alert=0
send_recovery=0

if [ "$status" != "ok" ]; then
  elapsed=$((now_epoch - last_alert_epoch))
  if [ "$previous_status" != "fail" ] || [ "$elapsed" -ge "$ALERT_COOLDOWN_SECONDS" ]; then
    send_alert=1
  fi
elif [ "$previous_status" = "fail" ]; then
  send_recovery=1
fi

notes_text=$(IFS=,; echo "${notes[*]}")

if [ "$send_alert" -eq 1 ]; then
  subject="[BLUEPRINTS][ALERT] healthcheck FAIL on ${host_name}"
  body=$(cat <<EOF
Timestamp (UTC): ${timestamp_utc}
Host: ${host_name}
Status: ${status}
Notes: ${notes_text}
State Age (s): ${state_age}
State File: ${STATE_FILE}
EOF
)
  channel_used=""
  if channel_used=$(send_notification "$subject" "$body"); then
    last_alert_epoch=$now_epoch
    notes+=("alert_${channel_used}_sent")
  else
    notes+=("alert_notify_failed")
  fi
fi

if [ "$send_recovery" -eq 1 ]; then
  subject="[BLUEPRINTS][RECOVERY] healthcheck OK on ${host_name}"
  body=$(cat <<EOF
Timestamp (UTC): ${timestamp_utc}
Host: ${host_name}
Status: ${status}
Notes: ${notes_text}
State Age (s): ${state_age}
EOF
)
  channel_used=""
  if channel_used=$(send_notification "$subject" "$body"); then
    notes+=("recovery_${channel_used}_sent")
  else
    notes+=("recovery_notify_failed")
  fi
fi

{
  echo "previous_status=${status}"
  echo "last_alert_epoch=${last_alert_epoch}"
} > "$TRACK_FILE"

final_notes=$(IFS=,; echo "${notes[*]}")
message="[${timestamp_utc}] status=${status} state_age_seconds=${state_age} notes=${final_notes}"
echo "$message" >> "$LOG_FILE"

if [ "$status" != "ok" ]; then
  echo "$message"
  exit 1
fi
