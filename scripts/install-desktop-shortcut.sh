#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
template=""
for candidate in \
  "$repo_root/scripts/DesignStudio.desktop" \
  "$repo_root/share/applications/designstudio.desktop"; do
  if [[ -f "$candidate" ]]; then
    template="$candidate"
    break
  fi
done
if [[ -z "$template" ]]; then
  echo "DesignStudio desktop-entry template was not found." >&2
  exit 2
fi
applications_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
destination="$applications_dir/designstudio.desktop"
staging="$applications_dir/designstudio-staging.desktop"

if [[ -n "${DESIGNSTUDIO_EXECUTABLE:-}" ]]; then
  executable="$DESIGNSTUDIO_EXECUTABLE"
else
  candidates=(
    "$HOME/.local/opt/designstudio/bin/DesignStudioLauncher"
  )
  executable=""
  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate" ]]; then
      executable="$(realpath "$candidate")"
      break
    fi
  done
fi

if [[ -z "$executable" || ! -x "$executable" ]]; then
  echo "No working FreeCAD-hosted DesignStudio launcher was found." >&2
  echo "Build/install the product or set DESIGNSTUDIO_EXECUTABLE to bin/DesignStudioLauncher." >&2
  exit 2
fi

if [[ "$(basename "$executable")" == "designstudio-qt-harness" ]]; then
  echo "Refusing to install the transitional Qt harness as the production shortcut." >&2
  exit 2
fi

if [[ -n "${DESIGNSTUDIO_ICON:-}" ]]; then
  icon="$DESIGNSTUDIO_ICON"
else
  icon_candidates=(
    "$HOME/.local/opt/designstudio/share/icons/hicolor/scalable/apps/designstudio.svg"
    "$repo_root/app/QtDesignStudio/resources/designstudio.svg"
  )
  icon="applications-engineering"
  for candidate in "${icon_candidates[@]}"; do
    if [[ -f "$candidate" ]]; then
      icon="$(realpath "$candidate")"
      break
    fi
  done
fi

desktop_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value// /\\ }"
  printf '%s' "$value"
}

mkdir -p "$applications_dir"
escaped_executable="$(desktop_escape "$executable")"
escaped_icon="$(desktop_escape "$icon")"
sed -e "s|@DESIGNSTUDIO_EXECUTABLE@|$escaped_executable|g" \
    -e "s|@DESIGNSTUDIO_ICON@|$escaped_icon|g" \
    "$template" >"$staging"

if command -v desktop-file-validate >/dev/null; then
  desktop-file-validate "$staging"
fi
chmod 0644 "$staging"
mv -f "$staging" "$destination"
if command -v update-desktop-database >/dev/null; then
  update-desktop-database "$applications_dir" >/dev/null 2>&1 || true
fi

# The user requested a visible desktop launcher as well as an application-menu
# entry. Keep both generated from the same validated file so they can never
# drift to different executables or branding.
desktop_dir="${DESIGNSTUDIO_DESKTOP_DIR:-}"
if [[ -z "$desktop_dir" ]] && command -v xdg-user-dir >/dev/null; then
  desktop_dir="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
fi
if [[ -z "$desktop_dir" && -d "$HOME/Desktop" ]]; then
  desktop_dir="$HOME/Desktop"
fi
if [[ -n "$desktop_dir" && -d "$desktop_dir" ]]; then
  desktop_destination="$desktop_dir/DesignStudio.desktop"
  install -m 0755 "$destination" "$desktop_destination"
  if command -v gio >/dev/null; then
    gio set "$desktop_destination" metadata::trusted true >/dev/null 2>&1 || true
  fi
  echo "Installed DesignStudio desktop icon: $desktop_destination"
fi

echo "Installed DesignStudio shortcut: $destination"
echo "Executable: $executable"
