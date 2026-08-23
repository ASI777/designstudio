#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 DESIGNSTUDIO_INSTALL_PREFIX" >&2
  exit 2
fi
prefix="$(realpath "$1")"
launcher="$prefix/bin/DesignStudioLauncher"
[[ -x "$launcher" ]] || { echo "missing installed launcher: $launcher" >&2; exit 2; }
strict="${DESIGNSTUDIO_STARTUP_TEST_STRICT:-0}"
if [[ "$strict" == "1" && -z "${WAYLAND_DISPLAY:-}" ]]; then
  echo "strict Wayland startup test requires an active WAYLAND_DISPLAY" >&2
  exit 2
fi

state_dir="$(mktemp -d /tmp/designstudio-wayland-state.XXXXXX)"
config_dir="$(mktemp -d /tmp/designstudio-wayland-config.XXXXXX)"
cache_dir="$(mktemp -d /tmp/designstudio-wayland-cache.XXXXXX)"
receipt="$state_dir/workspace-readiness.json"
shutdown_request="$state_dir/clean-shutdown-request.json"
shutdown_receipt="$state_dir/clean-shutdown-receipt.json"
token="$(date +%s%N)-$$"
cleanup() {
  if [[ -n "${host_pid:-}" ]] && kill -0 "$host_pid" 2>/dev/null; then
    kill -TERM "$host_pid" 2>/dev/null || true
  fi
  if [[ -n "${launcher_pid:-}" ]] && kill -0 "$launcher_pid" 2>/dev/null; then
    kill -TERM "$launcher_pid" 2>/dev/null || true
  fi
  if [[ -n "${launcher_pid:-}" ]] && kill -0 "$launcher_pid" 2>/dev/null; then
    for _ in {1..30}; do
      state="$(ps -o stat= -p "$launcher_pid" 2>/dev/null | tr -d ' ' || true)"
      [[ -z "$state" || "$state" == Z* ]] && break
      sleep 0.1
    done
    kill -KILL "$launcher_pid" 2>/dev/null || true
  fi
  rm -rf "$state_dir" "$config_dir" "$cache_dir"
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
if [[ "$strict" == "1" ]]; then
  export QT_QPA_PLATFORM=wayland
elif [[ "${DESIGNSTUDIO_USE_HOST_DISPLAY:-0}" == "1" ]]; then
  export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
else
  # CTest is a deterministic fallback-only check.  Real Wayland acceptance is
  # the explicit strict invocation and never uses this branch.
  export QT_QPA_PLATFORM=offscreen
fi
"$launcher" >/dev/null 2>&1 &
launcher_pid=$!
log="$state_dir/DesignStudio/startup.log"
ready=0
for _ in {1..300}; do
  if [[ -f "$receipt" ]] && kill -0 "$launcher_pid" 2>/dev/null; then
    ready=1
    break
  fi
  if ! kill -0 "$launcher_pid" 2>/dev/null; then
    break
  fi
  sleep 0.1
done
if [[ $ready -ne 1 ]]; then
  echo "DesignStudio Wayland launcher did not produce a post-activation workspace receipt" >&2
  [[ -f "$log" ]] && cat "$log" >&2
  exit 1
fi
receipt_pid="$(python3.14 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["pid"])' "$receipt")"
python3.14 "$(dirname "$0")/verify-startup-receipt.py" "$receipt" "$token" "$receipt_pid"
host_pid="$receipt_pid"
if [[ "$strict" == "1" ]]; then
  echo "DESIGNSTUDIO_WAYLAND_STARTUP_STRICT_OK pid=$host_pid"
else
  echo "DESIGNSTUDIO_WAYLAND_STARTUP_OK pid=$host_pid"
fi

# Request a clean Qt shutdown and require the launcher to reap the host normally.
python3.14 "$(dirname "$0")/request-clean-shutdown.py" \
  "$receipt" "$shutdown_request" "$shutdown_receipt" "$token" "$host_pid"
clean_exit=1
if ! timeout 10s bash -c "while state=\$(ps -o stat= -p '$launcher_pid' 2>/dev/null | tr -d ' '); do [[ -z \"\$state\" || \"\$state\" == Z* ]] && break; sleep 0.1; done"; then
  clean_exit=0
  if [[ "$strict" == "1" ]]; then
    echo "DesignStudio Wayland launcher did not shut down cleanly" >&2
    exit 1
  fi
  echo "DESIGNSTUDIO_OFFSCREEN_SHUTDOWN_CLEANUP=signal" >&2
  kill -TERM "$host_pid" 2>/dev/null || true
fi
wait "$launcher_pid" || status=$?
status="${status:-0}"
if [[ "$clean_exit" == "1" && "$status" -ne 0 ]]; then
  echo "DesignStudio Wayland launcher did not exit normally: $status" >&2
  exit 1
fi
if [[ "$clean_exit" == "1" ]]; then
grep -q 'phase=launcher_exit exit_status=0' "$log" || {
  echo "DesignStudio Wayland launcher did not record a clean exit" >&2
  cat "$log" >&2
  exit 1
}
echo "DESIGNSTUDIO_WAYLAND_SHUTDOWN_OK"
else
echo "DESIGNSTUDIO_WAYLAND_OFFSCREEN_DIAGNOSTIC_OK"
fi
