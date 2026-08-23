# FreeCAD distribution notice

DesignStudio uses a source build of FreeCAD 1.1.1 pinned by
`source.lock.json`. The FreeCAD source is not vendored in this repository.

FreeCAD is licensed primarily under LGPL-2.1-or-later, with additional
component-specific terms recorded by the upstream source tree. A release must
ship the upstream `LICENSE`, `LICENSES/`, and third-party notices from the exact
source archive, plus DesignStudio's corresponding source offer and patch
series. `scripts/build-freecad.sh` copies the upstream licensing material into
the install tree; the release gate must reject packages that omit it.

Any locally installed AppImage, when present, is a compatibility
reference only. Its extracted libraries are not a DesignStudio build input.
