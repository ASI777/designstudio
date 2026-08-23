# DesignStudio mechanical viewer

This is the optional cloud/browser presentation layer. It uses Three.js for
GPU-accelerated GLB display, orbit/fit/explode controls, and semantic view
modes. It does not replace the Qt desktop host or FreeCAD/OpenCascade: the
desktop remains the authority for CAD edits and exact B-Rep validation, while
the browser is a review client.

Run it with:

```sh
npm install
npm run typecheck
npm run dev
```

Load an approved mesh with `/?glb=/artifacts/candidate.glb`. The viewer expects
semantic metadata in glTF node `userData`/extras such as
`designStudioRole`, `semanticId`, and `explodeVector`. View modes are semantic,
not arbitrary color-box toggles: manufacturing mode hides reservation volumes
and shows the generated ribs/components, while presentation mode hides internal
engineering objects.

