# Contracted fabricator handoff

This directory captures the only remaining input needed to create production
CAM. It is deliberately not a fabrication package.

1. Send `TWORKS_PROFILE_REQUEST.md` to the selected fabricator and assembler.
2. Save their controlled capability/stackup document in this directory.
3. Copy `fabrication-profile-response.template.json`, replace every
   `REQUIRED_*` value from the controlled document, remove `DO_NOT_APPLY`, and
   record the named approval.
4. Apply the completed profile as a new project revision:

```bash
python3 tools/apply_fabrication_profile.py \
  acceptance/agent-workflow-controller/generated/agent-workflow-controller-v3.dsproj \
  acceptance/agent-workflow-controller/fabricator-handoff/fabrication-profile.json \
  acceptance/agent-workflow-controller/generated/agent-workflow-controller-production.dsproj
```

5. Rerun unified verification against that exact file, then use
   `tools/export_production_package.py`.

The template intentionally fails the DesignStudio schema until all controlled
manufacturer values and approval fields are supplied. Do not replace them with
nominal internet values or the engineering-target stackup.
