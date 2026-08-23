# Work Package 1 baseline

## Transitional baseline and production surface

The Qt 6 application at `app/QtDesignStudio` remains the deterministic baseline
and smoke-test harness. The production surface is the source-built FreeCAD
distribution containing `modules/DesignStudioGui`; it reuses the same UI library
inside FreeCAD's main window. `app/DesignStudio` is legacy reference material.

The C++20 engine under `core` and the Python gateway under
`services/ai-gateway` are supported dependencies of the Qt product.

## One build and verification command

From a clean Linux checkout with system prerequisites installed:

```bash
./build-linux.sh
```

The command is also used verbatim by CI. It performs, in order:

1. repository hygiene validation;
2. an out-of-source CMake configure and complete Qt/C++ build;
3. C++ tests and the `qt_app_smoke` headless startup test;
4. Python gateway contract tests using a locked dependency tree installed under
   the external work root.

The command prints `BASELINE_OK` only after every stage passes. Override the
external work location with `DS_WORK_ROOT`; it must not point inside the source
checkout.

## Mutable-data boundary

Source checkouts are read-only inputs to tools. Runtime locations are:

- build and test state: `$DS_WORK_ROOT`, otherwise the user's XDG cache;
- generated CAD/extractor artifacts: `$DESIGNSTUDIO_ARTIFACT_ROOT`, otherwise
  the user's XDG state directory;
- downloaded or generated datasets: `$DESIGNSTUDIO_DATA_ROOT`, otherwise the
  user's XDG data directory;
- immutable sample inputs: `testdata/` in the repository.

Generated output and mutable datasets must never be written beneath `app/`,
`core/`, `services/`, `swarm/`, or `tools/`. Repository hygiene checks enforce
that no build/output/data directories become tracked in source paths.

## Credentials

Secrets are user state, not project files. The Qt credentials dialog and swarm
launcher use `~/.config/designstudio/env`. Repository `.env` files and common
credential filenames are ignored, and CI fails if one becomes tracked. Never
place real credentials in `.env.example`, tests, fixtures, logs, or prompts.

## Worktree policy

Do not perform baseline, migration, or release work in a dirty checkout. Create
a dedicated branch-backed Git worktree from the intended commit, implement and
verify there, and leave the original checkout untouched. Before handoff, prove
both that the implementation worktree contains only intentional changes and
that a separate clean checkout passes `./build-linux.sh`.
