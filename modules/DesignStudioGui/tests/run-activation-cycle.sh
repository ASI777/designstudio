#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 DESIGNSTUDIO_INSTALL_PREFIX" >&2
  exit 2
fi

if [[ -z "${DESIGNSTUDIO_XVFB_ACTIVE:-}" \
      && -z "${DISPLAY:-}" \
      && "${QT_QPA_PLATFORM:-}" != "offscreen" \
      && "${DESIGNSTUDIO_USE_HOST_DISPLAY:-0}" != "1" ]]; then
  command -v xvfb-run >/dev/null || {
    echo "activation-cycle test requires DISPLAY, offscreen, or xvfb-run" >&2
    exit 2
  }
  export DESIGNSTUDIO_XVFB_ACTIVE=1
  export QT_QPA_PLATFORM=xcb
  exec xvfb-run -a "$0" "$@"
fi

prefix="$(realpath "$1")"
launcher="$prefix/bin/DesignStudioLauncher"
[[ -x "$launcher" ]] || { echo "missing installed launcher: $launcher" >&2; exit 2; }
state_dir="$(mktemp -d /tmp/designstudio-cycle-state.XXXXXX)"
config_dir="$(mktemp -d /tmp/designstudio-cycle-config.XXXXXX)"
cache_dir="$(mktemp -d /tmp/designstudio-cycle-cache.XXXXXX)"
receipt="$state_dir/workspace-readiness.json"
shutdown_request="$state_dir/clean-shutdown-request.json"
shutdown_receipt="$state_dir/clean-shutdown-receipt.json"
token="$(date +%s%N)-$$"
receipt_log="/tmp/designstudio-cycle-receipt.$$.log"
launcher_pid=""
host_pid=""
cleanup() {
  if [[ -n "$host_pid" ]] && kill -0 "$host_pid" 2>/dev/null; then
    kill -TERM "$host_pid" 2>/dev/null || true
  fi
  if [[ -n "$launcher_pid" ]] && kill -0 "$launcher_pid" 2>/dev/null; then
    kill -TERM "$launcher_pid" 2>/dev/null || true
    kill -KILL "$launcher_pid" 2>/dev/null || true
  fi
  rm -rf "$state_dir" "$config_dir" "$cache_dir" "$receipt_log"
}
trap cleanup EXIT

export XDG_STATE_HOME="$state_dir"
export XDG_CONFIG_HOME="$config_dir"
export XDG_CACHE_HOME="$cache_dir"
export DESIGNSTUDIO_DEPENDENCY_PREFIX="${DESIGNSTUDIO_DEPENDENCY_PREFIX:-$HOME/.local/opt/designstudio-deps}"
export DESIGNSTUDIO_STARTUP_RECEIPT="$receipt"
export DESIGNSTUDIO_STARTUP_RECEIPT_TOKEN="$token"
export DESIGNSTUDIO_SHUTDOWN_REQUEST="$shutdown_request"
export DESIGNSTUDIO_SHUTDOWN_RECEIPT="$shutdown_receipt"
export DESIGNSTUDIO_ACTIVATION_CYCLES=20
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"

"$launcher" >/dev/null 2>&1 &
launcher_pid=$!
log="$state_dir/DesignStudio/startup.log"
ready=0
for _ in {1..600}; do
  if [[ -f "$receipt" ]] && kill -0 "$launcher_pid" 2>/dev/null; then
    if python3.14 "$(dirname "$0")/verify-startup-receipt.py" \
        "$receipt" "$token" "-" 20 >"$receipt_log" 2>&1; then
      ready=1
      break
    fi
  fi
  if ! kill -0 "$launcher_pid" 2>/dev/null; then
    break
  fi
  sleep 0.1
done
if [[ $ready -ne 1 ]]; then
  echo "activation-cycle launcher did not produce a readiness receipt" >&2
  [[ -f "$log" ]] && cat "$log" >&2
  exit 1
fi
host_pid="$(python3.14 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["pid"])' "$receipt")"
python3.14 "$(dirname "$0")/verify-startup-receipt.py" "$receipt" "$token" "$host_pid" 20
python3.14 "$(dirname "$0")/request-clean-shutdown.py" \
  "$receipt" "$shutdown_request" "$shutdown_receipt" "$token" "$host_pid"
for _ in {1..100}; do
  state="$(ps -o stat= -p "$launcher_pid" 2>/dev/null | tr -d ' ' || true)"
  [[ -z "$state" || "$state" == Z* ]] && break
  sleep 0.1
done
if kill -0 "$launcher_pid" 2>/dev/null; then
  echo "DESIGNSTUDIO_ACTIVATION_OFFSCREEN_SHUTDOWN_CLEANUP=signal" >&2
  kill -TERM "$host_pid" 2>/dev/null || true
fi
wait "$launcher_pid" || status=$?
status="${status:-0}"
if [[ "$status" -ne 0 && "$status" -ne 143 ]]; then
  echo "activation-cycle launcher did not exit normally: $status" >&2
  exit 1
fi
echo "DESIGNSTUDIO_ACTIVATION_CYCLE_OK cycles=20 pid=$host_pid"
