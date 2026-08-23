#!/usr/bin/env bash
# Supported Linux baseline command: build Qt + C++, then run Qt/C++/Python tests.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"

if [[ -n "${DS_WORK_ROOT:-}" ]]; then
    WORK_ROOT="$DS_WORK_ROOT"
elif [[ -n "${XDG_CACHE_HOME:-}" ]]; then
    WORK_ROOT="$XDG_CACHE_HOME/designstudio/baseline"
else
    WORK_ROOT="$HOME/.cache/designstudio/baseline"
fi

ROOT_REAL="$(realpath -m "$ROOT")"
WORK_REAL="$(realpath -m "$WORK_ROOT")"
case "$WORK_REAL" in
    "$ROOT_REAL"|"$ROOT_REAL"/*)
        echo "ERROR: DS_WORK_ROOT must be outside the source checkout: $ROOT_REAL" >&2
        exit 2
        ;;
esac

for command_name in cmake "$PYTHON"; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "ERROR: required command not found: $command_name" >&2
        exit 2
    fi
done

BUILD_DIR="$WORK_REAL/cmake"
LOCK_FILE="$ROOT/services/ai-gateway/requirements-ci.lock.txt"
AGENTD_MANIFEST="$ROOT/services/designstudio-agentd/Cargo.toml"
AGENTD_BINARY="$ROOT/services/designstudio-agentd/target/release/designstudio-agentd"

mkdir -p "$WORK_REAL"

echo "[1/5] Repository hygiene"
"$PYTHON" "$ROOT/tools/check_repository_hygiene.py"

if command -v cargo >/dev/null 2>&1; then
    CARGO_TARGET_DIR="$WORK_REAL/rust-target" \
        cargo build --locked --release --manifest-path "$AGENTD_MANIFEST"
    AGENTD_BINARY="$WORK_REAL/rust-target/release/designstudio-agentd"
elif [[ ! -x "$AGENTD_BINARY" ]]; then
    echo "ERROR: cargo is unavailable and no verified designstudio-agentd artifact exists" >&2
    exit 2
fi

echo "[2/5] Configure C++ and supported Qt application"
cmake -S "$ROOT" -B "$BUILD_DIR" \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_TESTING=ON \
    -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
    -DDESIGNCORE_ENABLE_CUDA="${DESIGNCORE_ENABLE_CUDA:-OFF}" \
    -DDESIGNSTUDIO_AGENTD_TEST_EXECUTABLE="$AGENTD_BINARY"

echo "[3/5] Build C++ and Qt"
cmake --build "$BUILD_DIR" --config Release --parallel

# The CTest graph includes signed-release and signed-update regressions, so its
# interpreter must see the same locked Python dependencies as the service tests.
LOCK_HASH="$(sha256sum "$LOCK_FILE" | awk '{print $1}')"
PYTHON_DEPS="$WORK_REAL/python-deps/$LOCK_HASH"
if [[ ! -f "$PYTHON_DEPS/.complete" ]]; then
    mkdir -p "$PYTHON_DEPS"
    "$PYTHON" -m pip install \
        --disable-pip-version-check \
        --target "$PYTHON_DEPS" \
        --requirement "$LOCK_FILE"
    touch "$PYTHON_DEPS/.complete"
fi

echo "[4/5] Run C++ and headless Qt smoke tests"
PYTHONPATH="$PYTHON_DEPS${PYTHONPATH:+:$PYTHONPATH}" \
    ctest --test-dir "$BUILD_DIR" --build-config Release --output-on-failure

echo "[5/5] Run Python gateway and Hunyuan job-service contract tests"
PYTHONPATH="$PYTHON_DEPS${PYTHONPATH:+:$PYTHONPATH}" DS_AI_MOCK=1 "$PYTHON" \
    "$ROOT/services/ai-gateway/run_contract_tests.py"
(
    cd "$ROOT/cloud/hunyuan-omni-amd"
    PYTHONPATH="$PYTHON_DEPS${PYTHONPATH:+:$PYTHONPATH}" DS_HUNYUAN_MOCK=1 "$PYTHON" \
        test_service.py
    PYTHONPATH="$PYTHON_DEPS${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" test_multiview.py
)

echo "BASELINE_OK: Qt, C++, and Python checks passed"
echo "Build state: $WORK_REAL"
