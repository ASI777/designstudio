#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
lock_file="$repo_root/third_party/freecad/source.lock.json"
patch_dir="$repo_root/third_party/freecad/patches"
dependency_prefix="${DESIGNSTUDIO_DEPENDENCY_PREFIX:-$HOME/.local/opt/designstudio-deps}"

# Support the user-owned dependency bundle produced on machines where the
# desktop account cannot install build packages with sudo. System packages
# remain the normal fallback when this prefix is absent.
if [[ -d "$dependency_prefix/usr" ]]; then
  export PATH="$dependency_prefix/usr/bin:$PATH"
  export CMAKE_PREFIX_PATH="$dependency_prefix/usr${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
  dependency_libraries="$dependency_prefix/usr/lib/x86_64-linux-gnu:$dependency_prefix/usr/lib"
  export LD_LIBRARY_PATH="$dependency_libraries${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  dependency_pkgconfig="$dependency_prefix/usr/lib/x86_64-linux-gnu/pkgconfig:$dependency_prefix/usr/lib/pkgconfig:$dependency_prefix/usr/share/pkgconfig"
  export PKG_CONFIG_PATH="$dependency_pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
  dependency_python="$dependency_prefix/usr/lib/python3/dist-packages"
  export PYTHONPATH="$dependency_python${PYTHONPATH:+:$PYTHONPATH}"
  if [[ -z "${SWIG_LIB:-}" ]]; then
    for swig_library in "$dependency_prefix"/usr/share/swig/*; do
      if [[ -d "$swig_library" ]]; then
        export SWIG_LIB="$swig_library"
        break
      fi
    done
  fi

  # Debian development packages are normally installed at /usr and a few of
  # their generated CMake exports retain absolute paths. Relocate only the
  # package-owned Coin/PySide/Shiboken paths; Qt and LLVM remain host-provided.
  coin_cmake="$dependency_prefix/usr/lib/x86_64-linux-gnu/cmake/Coin-4.0.6"
  if [[ -d "$coin_cmake" ]]; then
    find "$coin_cmake" -name '*.cmake' -exec \
      sed -i \
        -e "s|$dependency_prefix$dependency_prefix|$dependency_prefix|g" \
        -e "s|\"/usr|\"$dependency_prefix/usr|g" \
        {} +
  fi
  binding_cmake="$dependency_prefix/usr/lib/x86_64-linux-gnu/cmake"
  if [[ -d "$binding_cmake" ]]; then
    find "$binding_cmake/PySide6" "$binding_cmake/Shiboken6" \
         "$binding_cmake/Shiboken6Tools" -name '*.cmake' -exec \
      sed -i \
        -e "s|$dependency_prefix$dependency_prefix|$dependency_prefix|g" \
        -e "s|\"/usr/include/PySide6|\"$dependency_prefix/usr/include/PySide6|g" \
        -e "s|\"/usr/include/shiboken6|\"$dependency_prefix/usr/include/shiboken6|g" \
        -e "s|\"/usr/share/PySide6|\"$dependency_prefix/usr/share/PySide6|g" \
        -e "s|\"/usr/lib/x86_64-linux-gnu/libpyside|\"$dependency_prefix/usr/lib/x86_64-linux-gnu/libpyside|g" \
        -e "s|\"/usr/lib/x86_64-linux-gnu/libshiboken|\"$dependency_prefix/usr/lib/x86_64-linux-gnu/libshiboken|g" \
        -e "s|\"/usr/lib/python3/dist-packages/PySide6|\"$dependency_prefix/usr/lib/python3/dist-packages/PySide6|g" \
        -e "s|\"/usr/lib/python3/dist-packages/shiboken6|\"$dependency_prefix/usr/lib/python3/dist-packages/shiboken6|g" \
        {} +
  fi
fi

if [[ "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Build the pinned FreeCAD source used by DesignStudio.

Environment:
  DS_FREECAD_CACHE   download cache (default: $XDG_CACHE_HOME/designstudio/freecad)
  DS_FREECAD_SOURCE  extracted source directory (default: ~/.cache/designstudio/freecad/source)
  DS_FREECAD_BUILD   CMake build directory (default: ~/.cache/designstudio/freecad/build)
  DS_FREECAD_INSTALL install prefix (default: ~/.local/opt/designstudio)
  DS_INTEGRATION_BUILD DesignStudio integration build (default: ~/.cache/designstudio/freecad/integration-build)
  DESIGNSTUDIO_DEPENDENCY_PREFIX optional rootless Ubuntu package prefix
  CMAKE_PREFIX_PATH  optional dependency prefix
  PYTHONPATH, LD_LIBRARY_PATH, SWIG_LIB may identify a rootless dependency prefix
  Python3_EXECUTABLE exact interpreter used by FreeCAD (default: /usr/bin/python3)
EOF
  exit 0
fi

for command in curl jq sha256sum tar cmake patch install; do
  command -v "$command" >/dev/null || {
    echo "missing required command: $command" >&2
    exit 2
  }
done

cache_root="${DS_FREECAD_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/designstudio/freecad}"
source_root="${DS_FREECAD_SOURCE:-$cache_root/source}"
build_root="${DS_FREECAD_BUILD:-$cache_root/build}"
install_root="${DS_FREECAD_INSTALL:-$HOME/.local/opt/designstudio}"
integration_build="${DS_INTEGRATION_BUILD:-$cache_root/integration-build}"
python_executable="${Python3_EXECUTABLE:-/usr/bin/python3}"
mkdir -p "$cache_root" "$source_root" "$build_root" "$install_root" "$integration_build"

case "$(realpath "$source_root")" in
  /|"$HOME"|"$(realpath "$repo_root")")
    echo "refusing unsafe DS_FREECAD_SOURCE: $source_root" >&2
    exit 2
    ;;
esac

download_verified() {
  local url="$1"
  local expected="$2"
  local output="$3"
  if [[ ! -f "$output" ]] || [[ "$(sha256sum "$output" | cut -d' ' -f1)" != "$expected" ]]; then
    curl --fail --location --retry 3 "$url" --output "$output"
  fi
  echo "$expected  $output" | sha256sum --check --status || {
    echo "digest mismatch: $output" >&2
    exit 3
  }
}

source_url="$(jq -r '.source.url' "$lock_file")"
source_sha="$(jq -r '.source.sha256' "$lock_file")"
source_archive="$cache_root/freecad-$(jq -r '.version' "$lock_file").tar.gz"
download_verified "$source_url" "$source_sha" "$source_archive"

if [[ -n "$(find "$source_root" -mindepth 1 -maxdepth 1 -print -quit)" ]] &&
   [[ ! -f "$source_root/.designstudio-source-digest" ]]; then
  echo "refusing to replace nonempty, unmanaged source directory: $source_root" >&2
  exit 2
fi
find "$source_root" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
tar -xzf "$source_archive" --strip-components=1 -C "$source_root"

while IFS=$'\t' read -r path url commit sha; do
  archive="$cache_root/submodule-$commit.tar.gz"
  download_verified "$url" "$sha" "$archive"
  mkdir -p "$source_root/$path"
  find "$source_root/$path" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
  tar -xzf "$archive" --strip-components=1 -C "$source_root/$path"
done < <(jq -r '.submodules[] | [.path, .url, .commit, .sha256] | @tsv' "$lock_file")

# The pinned archive stores this migration helper with CRLF endings. Normalize
# the patch target deterministically so the branded-startup patch applies on
# every supported host.
sed -i 's/\r$//' "$source_root/src/Mod/Start/StartMigrator.py"

while IFS= read -r patch_name; do
  [[ -z "$patch_name" || "$patch_name" == \#* ]] && continue
  patch --directory "$source_root" --strip=1 --forward <"$patch_dir/$patch_name"
done <"$patch_dir/series"
printf '%s\n' "$source_sha" >"$source_root/.designstudio-source-digest"

cmake -S "$source_root" -B "$build_root" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$install_root" \
  -DPython3_EXECUTABLE="$python_executable" \
  -DPython_EXECUTABLE="$python_executable" \
  -DPython_ROOT_DIR="$(dirname "$(dirname "$python_executable")")" \
  -DPython_FIND_STRATEGY=LOCATION \
  -DBUILD_TEST=OFF \
  -DENABLE_DEVELOPER_TESTS=OFF \
  -DINSTALL_TO_SITEPACKAGES=OFF \
  -DFREECAD_USE_PYSIDE=ON
cmake --build "$build_root" --parallel "${CMAKE_BUILD_PARALLEL_LEVEL:-2}"
cmake --install "$build_root"

cmake -S "$repo_root" -B "$integration_build" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$install_root" \
  -DPython3_EXECUTABLE="$python_executable" \
  -DPython_EXECUTABLE="$python_executable" \
  -DPython_ROOT_DIR="$(dirname "$(dirname "$python_executable")")" \
  -DPython_FIND_STRATEGY=LOCATION \
  -DBUILD_TESTING=OFF \
  -DDESIGNSTUDIO_BUILD_FREECAD_MODULE=ON \
  -DDESIGNSTUDIO_FREECAD_SOURCE_DIR="$source_root" \
  -DDESIGNSTUDIO_FREECAD_BUILD_DIR="$build_root" \
  -DDESIGNSTUDIO_FREECAD_INSTALL_DIR="$install_root"
cmake --build "$integration_build" --parallel "${CMAKE_BUILD_PARALLEL_LEVEL:-2}"
cmake --install "$integration_build"

# The lockfile is part of the distribution boundary. Cargo writes only to the
# integration build tree. A verified existing release artifact is accepted on
# desktop machines without a Rust toolchain; it is protocol-tested by the main
# build before this installer runs.
agentd_binary="$integration_build/rust-target/release/designstudio-agentd"
if command -v cargo >/dev/null; then
  cargo build --locked --release \
    --manifest-path "$repo_root/services/designstudio-agentd/Cargo.toml" \
    --target-dir "$integration_build/rust-target"
elif [[ -x "$repo_root/services/designstudio-agentd/target/release/designstudio-agentd" ]]; then
  agentd_binary="$repo_root/services/designstudio-agentd/target/release/designstudio-agentd"
else
  echo "missing cargo and no verified designstudio-agentd release artifact is available" >&2
  exit 2
fi
install -m 0755 \
  "$agentd_binary" \
  "$install_root/bin/designstudio-agentd"

license_target="$install_root/share/doc/DesignStudio/upstream/freecad"
mkdir -p "$license_target"
cp "$source_root/LICENSE" "$license_target/LICENSE"
if [[ -d "$source_root/LICENSES" ]]; then
  cp -R "$source_root/LICENSES" "$license_target/LICENSES"
fi
cp "$lock_file" "$license_target/source.lock.json"
cp "$repo_root/third_party/freecad/NOTICE.md" "$license_target/NOTICE.md"

# Install the current user's menu entry from the persistent installation.
DESIGNSTUDIO_EXECUTABLE="$install_root/bin/DesignStudioLauncher" \
DESIGNSTUDIO_ICON="$install_root/share/icons/hicolor/scalable/apps/designstudio.svg" \
  "$repo_root/scripts/install-desktop-shortcut.sh"
