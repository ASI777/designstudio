# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build

**Prerequisites (Windows):** Visual Studio 2022 (Desktop C++ + .NET workloads), CMake 3.20+, .NET 8 SDK.

```powershell
# C++ core (produces designcore.dll / libdesigncore.so)
cmake -B core/build -S core -G "Visual Studio 17 2022"
cmake --build core/build --config Release

# C# app + managed tests (Windows)
dotnet build DesignStudio.sln -c Debug
dotnet run --project app/DesignStudio
```

```bash
# Linux (CI path — core + managed tests only, no WPF)
cmake -B core/build -S core -DCMAKE_BUILD_TYPE=Release
cmake --build core/build -j
```

**Optional OCCT (3D B-rep kernel):**
```powershell
cmake -B core/build -DUSE_OCCT=ON -DCMAKE_TOOLCHAIN_FILE=<vcpkg>\scripts\buildsystems\vcpkg.cmake
```

If the app throws `DllNotFoundException: designcore`, copy `core/build/Release/designcore.dll` next to the exe.

## Tests

```powershell
# C++ engine tests (ctest, works on Linux too)
ctest --test-dir core/build -C Release --output-on-failure

# All managed tests (model, export, interop against the native engine)
dotnet test tests/DesignStudio.Tests/DesignStudio.Tests.csproj

# Single test class
dotnet test tests/DesignStudio.Tests/ --filter "ClassName=DesignStudio.Tests.FabExporterTests"
```

CI runs core tests on Linux (GCC) and Windows (MSVC), then managed tests on Linux and the full WPF build on Windows. See `.github/workflows/ci.yml`.

## Architecture

Two-layer architecture: a **C++20 shared library** (`core/`) for all geometry, routing, DRC, and physics; and a **C# .NET 8 WPF application** (`app/DesignStudio/`) that owns the UI, document model, project I/O, and fabrication export. They communicate exclusively through a flat C API.

### C++ core (`core/`)

| File | Responsibility |
|------|---------------|
| `include/designcore/c_api.h` + `src/c_api.cpp` | The only ABI boundary — opaque `DcBoardHandle`, plain structs, `DcStatus` return codes, two-phase result fetch (store on handle, fetch in second call — no fixed buffers). |
| `pcb_model.{h,cpp}` | N-layer board (up to 32 copper layers), pads, traces, vias (through/blind/buried/microvia), nets with stable integer ids, net classes. |
| `obstacle_index.{h,cpp}` | Uniform spatial grid used by the router, DRC, and pours for O(1) neighbour queries. |
| `router.{h,cpp}` | Multi-layer A* over (x, y, layer) with via insertion and cost. Returns a typed `RouteFail` on failure — never fails silently. |
| `drc.{h,cpp}` | 13 rule families: clearance (trace/pad/via combinations, layer-aware), board edge, min width, min drill, annular ring, hole-to-hole wall, unconnected net islands, length matching per net class. |
| `pour.{h,cpp}` | Scanline copper fill respecting net-class clearance. |
| `physics.{h,cpp}` | Hammerstad–Jensen microstrip, Cohn stripline (AGM), IPC-2141A diff pairs, skin/dielectric loss, IPC-2221 current, Onderdonk fusing, via L/C/R/θ, crosstalk, plane capacitance. |

**Coordinate system:** all native coordinates are **nanometres (`long` / `int64_t`)**. The P/Invoke constant `NativeCore.NmPerMm = 1_000_000` converts.

### C# app (`app/DesignStudio/`)

| Path | Responsibility |
|------|---------------|
| `Interop/NativeCore.cs` | P/Invoke declarations, status constants, `RouteStatusText`. |
| `Model/BoardDocument.cs` | Single source of truth. Before every native call it rebuilds the native board from scratch (`RebuildNative`) — there is no sync protocol or dirty flag. |
| `Model/ProjectIO.cs` | JSON project format v2 (`.dsproj`), atomic writes, autosave, v1 migration. |
| `Model/FootprintLibrary.cs` + `ParametricFootprints.cs` | JSON library per footprint in `%APPDATA%\DesignStudio\footprints\`; parametric IPC-7351 generators. |
| `Model/DatasheetImport.cs` | Trust boundary for the AI loop: validates `component/2` JSON, checks pad agreement, overlaps, annular ring, units before entering the library. |
| `Model/CircuitStateExport.cs` | Exports board state to `design-studio.circuit-state/1` JSON (for the AI advisor). |
| `Model/Ratsnest.cs` + `AutoPlacer.cs` | Connectivity state and simple auto-placement. |
| `Controls/PcbCanvas.cs` | Multi-layer WPF canvas (active layer bright, others dimmed). Select/Place/Route/AutoRoute tool states. |
| `Controls/Board3DBuilder.cs` | Regenerates 3D scene from PCB on every visit. |
| `Export/FabExporter.cs` | Gerber RS-274X (all copper layers, mask, paste, outline), Excellon split by plated span, BOM CSV, pick-and-place CSV. |
| `Dialogs.cs` + `PhysicsCalculatorDialog.cs` | Board setup (stackup, εr, tanδ, copper weight), net classes, pour, physics calculator. |

### AI design loop

`docs/datasheet-extractor/EXTRACTOR_PROMPT.md` → any vision LLM → `component/2` JSON → **Library → Import JSON** → place & wire → **File → Export Circuit State** → attach to Claude/Gemini with `docs/circuit-advisor/ADVISOR_PROMPT.md` → `advice/1` JSON → `Model/AdviceContract.cs` validates and applies. `Model/SiPiAdvisor.cs` is the deterministic SI/PI recommender (no LLM call needed for the standard failure modes). Mutable components live in the external user library; `testdata/component-library/` contains immutable samples.

### Project files

- `.dsproj` — JSON v2 board document (git-friendly).
- `.dsschem` — schematic (separate from PCB layout).
- `*-circuit-state.json` — exported circuit state snapshots for the AI advisor.

### Key invariants

- The C API never passes C++ types, exceptions, or STL across the boundary.
- Net ids are stable: deleting a net never shifts another net's id.
- `BoardDocument` rebuilds the native board before every native operation — UI/engine state drift is impossible by construction.
- Every `DcStatus` < 0 is an error; the C# side checks and surfaces human-readable text via `NativeCore.RouteStatusText`.
