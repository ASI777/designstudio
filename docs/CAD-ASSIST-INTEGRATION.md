# CAD-assist evidence connection

DesignStudio consumes `urn:design-studio:schema:cad-assist-session:1` as
read-only engineering evidence. The CAD Visual Helper is never allowed to write
or reload a `.dsproj` file.

## Producer workflow

1. Save the DesignStudio project. Legacy v1/v2 projects must be saved once so
   their stable `document_id` and `revision` are persisted.
2. Choose **Tools → Copy CAD Assist Binding**.
3. Paste the three exported environment variables into the shell that launches
   the CAD Visual Helper. They bind the output to the project ID, revision, and
   SHA-256 of the exact saved project bytes.
4. Capture and export drawing evidence in the helper.

If the project is edited or saved again, repeat the binding step. Evidence from
an older revision is intentionally stale.

## Consumer workflow

Choose **Tools → Load CAD Assist Evidence…**. DesignStudio independently checks:

- the complete CAD-assist schema and semantic graph;
- input size and finite numeric limits;
- stable IDs, source hashes, transforms, references, and path safety;
- `project.document_id` against the open project;
- `project.base_revision` against the in-memory revision;
- `project.base_sha256` against the exact saved `.dsproj` bytes.

DesignStudio re-hashes the file at attachment time. An external rewrite is
rejected even though automatic project reload is intentionally disabled.

Successful evidence appears in the **CAD Assist Evidence** dock. Loading it does
not modify the project. Any later persistent correction must be translated into
a typed ProposalContract, pass trusted host gates, and receive explicit user
acceptance.

The command-line equivalent is useful for CI:

```bash
DesignStudio --smoke-test --cad-assist session.json project.dsproj
```

It returns nonzero for unsaved, modified, mismatched, stale, or invalid input.
