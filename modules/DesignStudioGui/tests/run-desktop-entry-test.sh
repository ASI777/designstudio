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
  echo "strict desktop-entry test requires an active WAYLAND_DISPLAY" >&2
  exit 2
fi

desktop_file_validate="$(command -v desktop-file-validate || true)"
gtk_launch="$(command -v gtk-launch || true)"
[[ -n "$desktop_file_validate" && -n "$gtk_launch" ]] || {
  echo "desktop-file-validate and gtk-launch are required" >&2
  exit 2
}
for desktop in \
    "$HOME/.local/share/applications/designstudio.desktop" \
    "$HOME/Desktop/DesignStudio.desktop"; do
  [[ -f "$desktop" ]] || { echo "desktop entry is missing: $desktop" >&2; exit 1; }
  "$desktop_file_validate" "$desktop"
  exec_line="$(sed -n 's/^Exec=//p' "$desktop" | head -n 1)"
  [[ "$exec_line" == "$launcher %f" ]] || {
    echo "desktop entry Exec is stale: $desktop" >&2
    echo "expected: $launcher %f" >&2
    exit 1
  }
done

state_dir="$(mktemp -d /tmp/designstudio-desktop-state.XXXXXX)"
config_dir="$(mktemp -d /tmp/designstudio-desktop-config.XXXXXX)"
cache_dir="$(mktemp -d /tmp/designstudio-desktop-cache.XXXXXX)"
receipt="$state_dir/workspace-readiness.json"
shutdown_request="$state_dir/clean-shutdown-request.json"
shutdown_receipt="$state_dir/clean-shutdown-receipt.json"
token="$(date +%s%N)-$$"
cleanup() {
  if [[ -n "${host_pid:-}" ]] && kill -0 "$host_pid" 2>/dev/null; then
    kill -TERM "$host_pid" 2>/dev/null || true
  fi
  if [[ -n "${gtk_pid:-}" ]] && kill -0 "$gtk_pid" 2>/dev/null; then
    kill -TERM "$gtk_pid" 2>/dev/null || true
  fi
  if [[ -n "${gtk_pid:-}" ]] && kill -0 "$gtk_pid" 2>/dev/null; then
    kill -KILL "$gtk_pid" 2>/dev/null || true
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
launch_mode="gtk-launch"
if [[ "$strict" == "1" ]]; then
  export QT_QPA_PLATFORM=wayland
elif [[ "${DESIGNSTUDIO_ALLOW_OFFSCREEN_FALLBACK:-0}" == "1" ]]; then
  export QT_QPA_PLATFORM=offscreen
fi

find_test_process() {
  for pid in $(pgrep -f "$prefix/bin/DesignStudio" || true); do
    if [[ -r "/proc/$pid/environ" ]] \
        && tr '\0' '\n' <"/proc/$pid/environ" \
            | grep -Fxq "XDG_STATE_HOME=$state_dir"; then
      printf '%s\n' "$pid"
      return 0
    fi
  done
  return 1
}

launch_with_gtk() {
  "$gtk_launch" designstudio >/dev/null 2>&1 &
  gtk_pid=$!
}

if [[ "$strict" != "1" \
      && -z "${DESIGNSTUDIO_DESKTOP_XVFB_ACTIVE:-}" \
      && "${DESIGNSTUDIO_USE_HOST_DISPLAY:-0}" != "1" \
      && "${QT_QPA_PLATFORM:-}" != "offscreen" ]]; then
  command -v xvfb-run >/dev/null || { echo "DISPLAY or xvfb-run required" >&2; exit 2; }
  export DESIGNSTUDIO_DESKTOP_XVFB_ACTIVE=1
  exec xvfb-run -a bash "$0" "$prefix"
fi

# Strict acceptance is deliberately a real desktop-entry launch. There is no
# direct-launch or offscreen fallback in this branch.
launch_with_gtk
log="$state_dir/DesignStudio/startup.log"
ready=0
for _ in {1..300}; do
  if [[ -f "$receipt" ]]; then
    ready=1
    break
  fi
  # gtk-launch is a dispatcher and normally exits immediately; the launched
  # DesignStudio process owns the receipt and may still be starting.
  sleep 0.1
done

if [[ $ready -ne 1 && "$strict" != "1" \
      && "${DESIGNSTUDIO_ALLOW_OFFSCREEN_FALLBACK:-0}" == "1" ]]; then
  echo "DESIGNSTUDIO_DESKTOP_ENTRY_GTK_LAUNCH_FALLBACK=direct" >&2
  launch_mode="direct-fallback"
  "$launcher" >/dev/null 2>&1 &
  gtk_pid=$!
  for _ in {1..300}; do
    if [[ -f "$receipt" ]]; then
      ready=1
      break
    fi
    if ! kill -0 "$gtk_pid" 2>/dev/null; then
      break
    fi
    sleep 0.1
  done
fi

if [[ $ready -ne 1 ]]; then
  echo "gtk-launch did not produce a post-activation workspace receipt" >&2
  [[ -f "$log" ]] && cat "$log" >&2
  exit 1
fi
receipt_pid="$(python3.14 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["pid"])' "$receipt")"
python3.14 "$(dirname "$0")/verify-startup-receipt.py" "$receipt" "$token" "$receipt_pid"
host_pid="$receipt_pid"
if [[ "$strict" == "1" ]]; then
  echo "DESIGNSTUDIO_DESKTOP_ENTRY_STRICT_OK pid=$host_pid launch=gtk-launch"
else
  echo "DESIGNSTUDIO_DESKTOP_ENTRY_OK pid=$host_pid launch=$launch_mode"
fi

python3.14 "$(dirname "$0")/request-clean-shutdown.py" \
  "$receipt" "$shutdown_request" "$shutdown_receipt" "$token" "$host_pid"
for _ in {1..100}; do
  state="$(ps -o stat= -p "$host_pid" 2>/dev/null | tr -d ' ' || true)"
  [[ -z "$state" || "$state" == Z* ]] && break
  sleep 0.1
done
state="$(ps -o stat= -p "$host_pid" 2>/dev/null | tr -d ' ' || true)"
if [[ -n "$state" && "$state" != Z* ]]; then
  if [[ "$strict" == "1" ]]; then
    echo "desktop-launched DesignStudio did not exit cleanly" >&2
    exit 1
  fi
  echo "DESIGNSTUDIO_DESKTOP_OFFSCREEN_SHUTDOWN_CLEANUP=signal" >&2
  kill -TERM "$host_pid" 2>/dev/null || true
fi
if [[ "$strict" != "1" ]]; then
  echo "DESIGNSTUDIO_DESKTOP_ENTRY_OFFSCREEN_DIAGNOSTIC_OK"
  exit 0
fi
for _ in {1..100}; do
  grep -q 'phase=launcher_exit exit_status=0' "$log" 2>/dev/null && break
  sleep 0.1
done
grep -q 'phase=launcher_exit exit_status=0' "$log" || {
  echo "desktop-launched DesignStudio did not record a clean exit" >&2
  cat "$log" >&2
  exit 1
}
echo "DESIGNSTUDIO_DESKTOP_ENTRY_SHUTDOWN_OK"
