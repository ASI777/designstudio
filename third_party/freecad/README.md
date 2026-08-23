# Pinned FreeCAD source

`source.lock.json` is the authority for the upstream archive and every git
submodule required by the production build. The archive digest and git tag
commit were verified against the official FreeCAD GitHub repository.

Run `scripts/build-freecad.sh`. The script downloads into an external cache,
verifies every digest before extraction, applies the ordered patch series, and
configures a Qt 6/Python GUI build. Build, source, cache, and install directories
default outside the repository and can be overridden with environment
variables documented by `--help`.

The host dependency versions used by the verified Ubuntu build are recorded in
`dependencies.lock.json`. Containerized release builds must provide those exact
versions or update the lock and verification evidence deliberately.
