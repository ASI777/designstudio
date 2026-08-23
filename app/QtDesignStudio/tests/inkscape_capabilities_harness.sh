#!/usr/bin/env bash
# inkscape_capabilities_harness.sh
#
# Verifies that the Inkscape capabilities DesignStudio relies on (via
# InkscapeHost) are actually present AND functional in the staged tree:
#
#   24 tools · 1008 actions/commands · 179 extensions · 213 filters
#   + LPE / symbol / template / palette libraries
#
# It mirrors InkscapeHost::launch() exactly (same root + env), so a PASS here
# means the embedded editor will have the same capabilities. Each check prints
# PASS/FAIL and, on failure, a concrete diagnosis. Exit code = number of fails.
#
# Usage:  ./inkscape_capabilities_harness.sh
#         DESIGNSTUDIO_INKSCAPE_ROOT=/path/to/usr ./inkscape_capabilities_harness.sh

set -u

# ── Mirror InkscapeHost::inkscapeRoot() + launch env ─────────────────────────
ROOT="${DESIGNSTUDIO_INKSCAPE_ROOT:-$HOME/Desktop/pcb_designer/usr}"
BIN="$ROOT/bin/inkscape"
LIBS="$ROOT/lib/x86_64-linux-gnu/inkscape"
DATA="$ROOT/share"
SHARE="$DATA/inkscape"

export GDK_BACKEND="x11"
export INKSCAPE_DATADIR="$DATA"
export LD_LIBRARY_PATH="$LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# Expected counts (from the staged Inkscape 1.4.3 inventory)
EXP_TOOLS=24
EXP_ACTIONS=1008
EXP_EXT=179
EXP_FILTERS=213
EXP_SYMBOLS=21
EXP_TEMPLATES=82
EXP_PALETTES=22

PASS=0; FAIL=0
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
ACT="$TMP/actions.txt"

c_g(){ printf '\033[32m'; }; c_r(){ printf '\033[31m'; }; c_y(){ printf '\033[33m'; }; c_0(){ printf '\033[0m'; }
ok(){   c_g; echo "  PASS  $1"; c_0; PASS=$((PASS+1)); }
bad(){  c_r; echo "  FAIL  $1"; c_0; FAIL=$((FAIL+1)); }
diag(){ c_y; echo "        ↳ diagnose: $1"; c_0; }
hdr(){  echo; echo "── $1 ─────────────────────────────────────────────"; }

# assert_count <label> <actual> <expected> <diagnosis-if-wrong>
assert_count(){
  local label="$1" actual="$2" expected="$3" why="$4"
  if [ "$actual" = "$expected" ]; then ok "$label = $actual"; else
    bad "$label = $actual (expected $expected)"; diag "$why"; fi
}

echo "Inkscape capability harness"
echo "root: $ROOT"

# ── 0. Binary runs (the foundation for everything) ───────────────────────────
hdr "0. Engine"
if [ ! -x "$BIN" ]; then
  bad "binary not executable: $BIN"
  diag "Set DESIGNSTUDIO_INKSCAPE_ROOT to the staged tree (…/usr) or restage Inkscape."
  echo; echo "ABORT: no engine."; exit 99
fi
MISSING="$(ldd "$BIN" 2>/dev/null | grep -i 'not found')"
VER="$("$BIN" --version 2>/dev/null | head -1)"
if echo "$VER" | grep -q "Inkscape 1.4"; then ok "engine runs — $VER"; else
  bad "engine did not report a 1.4 version (got: '${VER:-<none>}')"
  [ -n "$MISSING" ] && diag "missing shared libs:\n$MISSING" \
                    || diag "LD_LIBRARY_PATH=$LIBS may be wrong, or libinkscape_base.so is absent."
fi

# Pull the action list once (depends on INKSCAPE_DATADIR for extension actions).
"$BIN" --action-list 2>/dev/null > "$ACT"
NACT=$(wc -l < "$ACT")

# ── 1. 24 toolbox tools ──────────────────────────────────────────────────────
hdr "1. Tools (toolbox)"
TOOL_UI="$SHARE/ui/toolbar-tool.ui"
if [ -f "$TOOL_UI" ]; then
  NTOOLS=$(grep -oE '<property name="action-target">[^<]+</property>' "$TOOL_UI" \
           | sed -E 's/.*">//; s/<.*//' | tr -d "'" | sort -u | wc -l)
  assert_count "toolbox tools" "$NTOOLS" "$EXP_TOOLS" \
    "ui/toolbar-tool.ui present but tool count differs — Inkscape version mismatch (expected 1.4.3)."
  grep -oE '<property name="action-target">[^<]+</property>' "$TOOL_UI" \
       | sed -E 's/.*">//; s/<.*//' | tr -d "'" | sort -u | paste -sd' ' | sed 's/^/        tools: /'
else
  bad "tool UI missing: $TOOL_UI"; diag "INKSCAPE_DATADIR=$DATA wrong, or ui/ not staged."
fi

# ── 2. 1008 actions / commands ───────────────────────────────────────────────
hdr "2. Actions / commands"
assert_count "actions (--action-list)" "$NACT" "$EXP_ACTIONS" \
  "If far below 1008, extension actions didn't register → INKSCAPE_DATADIR ($DATA) is wrong or extensions/ missing."
# Functional: a core action actually executes (load → select-all → object-to-path → export)
printf '%s' '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect x="2" y="2" width="10" height="6"/></svg>' > "$TMP/in.svg"
"$BIN" --actions="select-all;object-to-path" --export-type=svg \
       --export-filename="$TMP/out.svg" "$TMP/in.svg" >/dev/null 2>&1
if [ -f "$TMP/out.svg" ] && grep -q '<path' "$TMP/out.svg"; then
  ok "action engine executes (rect → path via object-to-path)"
else
  bad "action engine did not transform rect→path"
  diag "Core command pipeline broken — check that --actions runs headlessly (no DISPLAY needed for export)."
fi

# ── 3. 179 extensions ────────────────────────────────────────────────────────
hdr "3. Extensions"
EXTDIR="$SHARE/extensions"
NINX=$(find "$EXTDIR" -name '*.inx' 2>/dev/null | wc -l)
assert_count "extension descriptors (.inx)" "$NINX" "$EXP_EXT" \
  "extensions/ not fully staged at $EXTDIR."
# extension actions registered in the engine (the .noprefs+main entries)
NEFFECT=$(grep -cE '^org\.inkscape\.effect|^org\.ekips' "$ACT")
if [ "$NEFFECT" -ge 400 ]; then ok "extension actions registered ($NEFFECT)"; else
  bad "only $NEFFECT extension actions registered"; diag "datadir/extensions mismatch — effect menu will be sparse."; fi
# Functional: the Python extension stack imports (effects fail silently without it)
PYERR="$(PYTHONPATH="$EXTDIR" python3 -c 'import inkex' 2>&1)"
if [ -z "$PYERR" ]; then ok "inkex Python stack imports (extensions can run)"; else
  bad "inkex import failed"; diag "extensions present but won't EXECUTE — install Python deps. Error: ${PYERR##*$'\n'}"; fi

# ── 4. 213 filters ───────────────────────────────────────────────────────────
hdr "4. Filters"
NFILT=$(grep -rhoE '<filter[^>]*inkscape:label="[^"]*"' "$SHARE/filters" 2>/dev/null | wc -l)
assert_count "filter presets" "$NFILT" "$EXP_FILTERS" \
  "share/inkscape/filters not staged or version differs."

# ── 5. LPE ───────────────────────────────────────────────────────────────────
hdr "5. Live Path Effects"
NLPE=$(grep -ciE 'lpe|path-effect' "$ACT")
if [ "$NLPE" -ge 1 ]; then ok "LPE actions present ($NLPE)"; else
  bad "no LPE actions"; diag "path-effect subsystem missing from this build."; fi

# ── 6. Libraries: symbols / templates / palettes ─────────────────────────────
hdr "6. Libraries"
assert_count "symbol sets"     "$(ls "$SHARE/symbols"/*.svg   2>/dev/null | wc -l)" "$EXP_SYMBOLS"   "share/inkscape/symbols not staged."
assert_count "templates"       "$(ls "$SHARE/templates"/*.svg 2>/dev/null | wc -l)" "$EXP_TEMPLATES" "share/inkscape/templates not staged."
assert_count "palettes (.gpl)" "$(ls "$SHARE/palettes"/*.gpl  2>/dev/null | wc -l)" "$EXP_PALETTES"  "share/inkscape/palettes not staged."

# ── Summary ──────────────────────────────────────────────────────────────────
hdr "Summary"
echo "  PASS: $PASS    FAIL: $FAIL"
[ "$FAIL" -eq 0 ] && { c_g; echo "  All embedded-Inkscape capabilities verified."; c_0; } \
                  || { c_r; echo "  $FAIL check(s) failed — see diagnoses above."; c_0; }
exit "$FAIL"
