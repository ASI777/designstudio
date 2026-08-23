#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 DESIGNSTUDIO_INSTALL_PREFIX" >&2
  exit 2
fi

if [[ -z "${DESIGNSTUDIO_XVFB_ACTIVE:-}" \
      && "${DESIGNSTUDIO_USE_HOST_DISPLAY:-0}" != "1" ]]; then
  if ! command -v xvfb-run >/dev/null; then
    echo "native FreeCAD GUI smoke requires DISPLAY or xvfb-run" >&2
    exit 2
  fi
  export DESIGNSTUDIO_XVFB_ACTIVE=1
  export QT_QPA_PLATFORM=xcb
  exec xvfb-run -a "$0" "$@"
fi

prefix="$(realpath "$1")"
dependency_prefix="${DESIGNSTUDIO_DEPENDENCY_PREFIX:-$HOME/.local/opt/designstudio-deps}"
smoke_root="$(mktemp -d /tmp/designstudio-native-smoke.XXXXXX)"
cleanup_smoke_root() {
  case "$smoke_root" in
    /tmp/designstudio-native-smoke.*) rm -rf -- "$smoke_root" ;;
    *) echo "refusing to remove unexpected smoke root: $smoke_root" >&2 ;;
  esac
}
trap cleanup_smoke_root EXIT
test_mod_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
test_module="$test_mod_root/NativeSmoke"
launcher="$prefix/bin/DesignStudioLauncher"
host_binary="$prefix/bin/DesignStudio"
if [[ ! -x "$launcher" && ! -x "$host_binary" ]]; then
  echo "installed DesignStudio launcher/host binary is missing under $prefix" >&2
  exit 2
fi
if [[ -x "$launcher" ]]; then
  host_command=("$launcher")
else
  host_command=("$host_binary")
fi
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
export XDG_STATE_HOME="$smoke_root/state"
export XDG_CACHE_HOME="$smoke_root/cache"
export XDG_CONFIG_HOME="$smoke_root/config"
export XDG_DATA_HOME="$smoke_root/data"
export PYTHONNOUSERSITE=1
export PYTHONUSERBASE="$smoke_root/python-userbase"
dependency_library_path="$dependency_prefix/usr/lib/x86_64-linux-gnu:$dependency_prefix/usr/lib"
export LD_LIBRARY_PATH="$prefix/lib:$dependency_library_path${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export QT_PLUGIN_PATH="${QT_PLUGIN_PATH:-/usr/lib/x86_64-linux-gnu/qt6/plugins}"
dependency_python_path="$dependency_prefix/usr/lib/python3/dist-packages"
if [[ -d "$dependency_python_path" ]]; then
  export PYTHONPATH="$dependency_python_path${PYTHONPATH:+:$PYTHONPATH}"
fi
python_args=()
IFS=: read -r -a python_paths <<<"${PYTHONPATH:-}"
for path in "${python_paths[@]}"; do
  [[ -n "$path" ]] && python_args+=(--python-path "$path")
done

run_native_smoke() {
  local output="$1"
  set +e
  "${host_command[@]}" \
    "${python_args[@]}" \
    --module-path "$test_module" \
    --user-cfg "$smoke_root/user.cfg" \
    --system-cfg "$smoke_root/system.cfg" 2>&1 | tee "$output"
  local host_status="${PIPESTATUS[0]}"
  set -e
  # QApplication.exit(1) is not propagated reliably by every FreeCAD build.
  # The fixture marker is therefore authoritative; a zero process status can
  # never hide a failed or incomplete native startup assertion.
  if grep -q '^DESIGNSTUDIO_FREECAD_NATIVE_SMOKE_FAILED:' "$output"; then
    return 1
  fi
  if [[ "$host_status" -ne 0 ]] || ! grep -q '^DESIGNSTUDIO_FREECAD_NATIVE_SMOKE_OK$' "$output"; then
    return 1
  fi
  return 0
}

status=0
run_native_smoke "$smoke_root/xcb.log" || status=$?
if [[ "$status" -ne 0 && "${DESIGNSTUDIO_ALLOW_OFFSCREEN_FALLBACK:-0}" == "1" \
      && "${QT_QPA_PLATFORM:-}" == "xcb" ]]; then
  # Some restricted CI sandboxes expose an Xvfb display but deny the xcb
  # cursor extension.  Re-run the same native FreeCAD fixture offscreen; this
  # remains a real workbench/MDI assertion, never the Qt-only harness.
  export QT_QPA_PLATFORM=offscreen
  status=0
  run_native_smoke "$smoke_root/offscreen.log" || status=$?
fi
exit "$status"
