#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 DESIGNSTUDIO_INSTALL_PREFIX" >&2
  exit 2
fi
prefix="$(realpath "$1")"
launcher="$prefix/bin/DesignStudioLauncher"
[[ -x "$launcher" ]] || { echo "missing installed launcher: $launcher" >&2; exit 2; }
state_dir="$(mktemp -d /tmp/designstudio-profile-state.XXXXXX)"
config_dir="$(mktemp -d /tmp/designstudio-profile-config.XXXXXX)"
cache_dir="$(mktemp -d /tmp/designstudio-profile-cache.XXXXXX)"
trap 'rm -rf "$state_dir" "$config_dir" "$cache_dir"' EXIT
export XDG_STATE_HOME="$state_dir"
export XDG_CONFIG_HOME="$config_dir"
export XDG_CACHE_HOME="$cache_dir"
export DESIGNSTUDIO_DEPENDENCY_PREFIX="${DESIGNSTUDIO_DEPENDENCY_PREFIX:-$HOME/.local/opt/designstudio-deps}"
export DESIGNSTUDIO_STARTUP_PROBE=1

run_probe() {
  "$launcher" >/dev/null 2>&1
  status=$?
  [[ "$status" -lt 128 ]] || {
    echo "profile probe crashed: $status" >&2
    return 1
  }
  [[ -f "$state_dir/DesignStudio/startup.log" ]] || return 1
  return "$status"
}

# clean profile
run_probe
# persisted vendor credentials: the desktop launcher must parse only allowlisted
# values, export them to the child process, and repair owner-only permissions.
mkdir -p "$config_dir/designstudio"
token_file="$cache_dir/dk_tokens.json"
printf '%s\n' '{"access_token":"fixture"}' >"$token_file"
chmod 664 "$token_file"
printf '%s\n' \
  "DIGIKEY_CLIENT_ID='fixture-id'" \
  "DIGIKEY_CLIENT_SECRET='fixture-secret'" \
  "MOUSER_API_KEY='fixture-key'" \
  "DIGIKEY_TOKEN_FILE='$token_file'" >"$config_dir/designstudio/env"
chmod 664 "$config_dir/designstudio/env"
run_probe
[[ "$(stat -c '%a' "$config_dir/designstudio/env")" == "600" ]] || {
  echo "credential file permissions were not repaired" >&2; exit 1;
}
[[ "$(stat -c '%a' "$token_file")" == "600" ]] || {
  echo "DigiKey token permissions were not repaired" >&2; exit 1;
}
grep -q 'phase=credentials_ready digikey=configured mouser=configured' \
  "$state_dir/DesignStudio/startup.log" || {
  echo "persisted vendor credentials were not loaded" >&2; exit 1;
}
# Unsafe shell content is rejected without evaluating or printing its value.
printf '%s\n' "MOUSER_API_KEY='unsafe;value'" >"$config_dir/designstudio/env"
set +e
run_probe
status=$?
set -e
[[ "$status" -eq 78 ]] || { echo "unsafe credential unexpectedly passed: $status" >&2; exit 1; }
grep -q 'phase=credential_error.*reason=unsafe-value' "$state_dir/DesignStudio/startup.log" || {
  echo "unsafe credential diagnostic was not logged" >&2; exit 1;
}
printf '%s\n' "DIGIKEY_TOKEN_FILE='$token_file'" >"$config_dir/designstudio/env"
# migrated profile: retain an old-version marker and require the same stable
# preflight result without sharing the clean profile directory.
printf '%s\n' 'version=0.9' >"$config_dir/DesignStudio/v1-1/migrated.conf"
run_probe
# corrupt profile: malformed data must not turn into a native crash.
printf '%s\n' '{not-json' >"$config_dir/DesignStudio/v1-1/profile.json"
set +e
run_probe
status=$?
set -e
[[ "$status" -eq 78 ]] || { echo "corrupt profile unexpectedly passed: $status" >&2; exit 1; }
grep -q 'reason=corrupt-json' "$state_dir/DesignStudio/startup.log" || {
  echo "corrupt profile diagnostic was not logged" >&2
  exit 1
}
rm -f "$config_dir/DesignStudio/v1-1/profile.json"
# unwritable profile: a file in place of XDG_CONFIG_HOME produces status 78.
blocked="$(mktemp /tmp/designstudio-blocked-profile.XXXXXX)"
if XDG_CONFIG_HOME="$blocked" "$launcher" >/dev/null 2>&1; then
  rm -f "$blocked"
  echo "unwritable profile unexpectedly passed" >&2
  exit 1
else
  status=$?
  rm -f "$blocked"
  [[ "$status" -eq 78 ]] || { echo "unexpected unwritable status: $status" >&2; exit 1; }
fi
# missing dependency prefix: the launcher must diagnose before FreeCAD starts.
bad_dependencies="$(mktemp -d /tmp/designstudio-missing-deps.XXXXXX)"
set +e
DESIGNSTUDIO_DEPENDENCY_PREFIX="$bad_dependencies" "$launcher" >/dev/null 2>&1
status=$?
set -e
rm -rf "$bad_dependencies"
[[ "$status" -eq 78 ]] || { echo "unexpected missing-dependency status: $status" >&2; exit 1; }
grep -q 'phase=dependency_error' "$state_dir/DesignStudio/startup.log" || {
  echo "missing dependency diagnostic was not logged" >&2
  exit 1
}
echo "DESIGNSTUDIO_PROFILE_STARTUP_OK"
