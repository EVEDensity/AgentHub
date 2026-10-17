# ADR-0126: Freeze operator-reviewed document Skills before model use

> Status: accepted\
> Owner: Runner and extension maintainers\
> Last reviewed: 2026-10-18\
> Scope: M2a original Skills, source provenance, packaging and explicit Native reads

## Context

The existing workspace `skill_list` and `skill_load` read simple SKILL.md files.
Their legacy scanners do not establish an operator-reviewed source revision,
content inventory or license. Combining them with a reviewed label would allow
workspace precedence to change the selected instructions. Skills are documents,
not another model loop, business state store or source of capability grants.

The accepted [composition plan](../../roadmaps/composable-agent-platform-plan.md)
calls for three original packages and progressive disclosure based on the
[Agent Skills format](https://agentskills.io/specification). This slice supports
a bounded text-only subset rather than arbitrary executable Skill packages.

## Decision

Keep one copy of three Apache-2.0 document packages under
`app/services/reviewed_skills/packages`: repository-fix, code-review and
source-traceable-research. Each contains SKILL.md, the full license and one
`references/output.md` template. There are no scripts or remote installers.

An operator-controlled catalog records version, license, required capabilities,
source URL, a real 40-character Git content revision, an exact file inventory,
normalized UTF-8 byte counts, file SHA-256 and package SHA-256. Original content
is committed before the index pins that commit, avoiding a self-referential SHA.
The package digest covers the canonical sorted file manifest, excluding the
outer index. Source pin tests compare every file with the actual Git blobs.
The runtime does not contact source URLs or independently authenticate a remote
publisher; trust in the bundled index follows trust in the application release.

PyYAML is a direct pinned runtime dependency. The new parser validates the
required format fields, directory/name match, metadata string values, explicit
version and license. Duplicate keys, aliases, unsupported fields, oversized or
invalid text and unsupported resource paths fail loading. `allowed-tools` is
informational. This parser does not silently reinterpret legacy workspace files
or claim full Agent Skills ecosystem compatibility.

Loading verifies the complete bounded inventory once and freezes immutable
documents and metadata. Text is canonical UTF-8 with CRLF/CR normalized to LF;
digests describe those canonical bytes, not a platform-specific checkout.
Filesystem links and extra files are rejected. Operator root injection exists
for reviewed local deployments/tests and must not accept model/API arguments.
After construction, workspace mutations cannot replace the catalog's bytes.

The model-facing read pair retains `skill_list` and `skill_load`:

- List returns metadata and capability gaps, without bodies or reference text.
- Load returns only the explicitly named body, or its explicitly named listed
  reference. `resource` is optional and limited to `references/*.md` one level
  below the package. Each read checks the operator-supplied capability inventory.
- Both functions are stateless. Reading a reference is an explicit selection;
  it does not depend on another Mission's prior activation state.
- Successful responses include provenance/digests. Oversized output returns an
  explicit error instead of truncated success. The current three packages fit
  the Desktop's 4,000-character result budget, including provenance.
- Tool descriptions bind the catalog and available-capability digests, so
  existing Desktop recovery material changes when either input changes.

`build_desktop_runner_tools` accepts an explicit reviewed catalog and capability
inventory. This replaces its two workspace Skill readers, keeping tool names,
other tool schemas and permission policy unchanged. A missing or invalid catalog
fails explicitly; reviewed mode never falls back to home/cwd/workspace scans.
Default Desktop/CLI/Web and legacy API selection remain on their existing paths.

Capability declarations never create Contract grants or tool handlers. The
operator must supply the actual inventory permitted by the selected composition;
the document reader cannot authorize the workflow it describes. Strict Native
composition continues to require its existing exact WorkUnit/Contract/tool
admission. Mission Control remains the only owner of durable business state.

## Verification and limits

Setuptools package-data explicitly includes the index and all nine resources.
Tests build and install an actual wheel without network dependency resolution,
change away from the checkout, and load all three bodies and references. The
Linux and Windows CI source checkouts retain history to verify source commit
bytes. Tests also cover frozen content, malformed metadata, inventory/digest
changes, resource boundaries, missing capabilities, same-name workspace
packages, output limits and unchanged default behavior.

A real Native/loopback HTTP/SQLite budget/Runner/CAS/Verifier scenario exercises
metadata, one selected body, one reference and a separately authorized local
read. Unselected bodies do not enter model requests; Contract grants and tool
schemas remain unchanged. Its deterministic model and artifact-set PASS prove
protocol composition and byte integrity, not review quality, research accuracy
or paid-provider behavior.

There is no database migration or new public API schema. Disabling injection
stops reviewed reads for new compositions. Existing artifacts and evidence keep
their meaning; a changed catalog cannot reuse a matching old recovery context.
Frozen desktop executables still need a separate resource collection/release
check. This slice does not complete the M2 executor comparison or browser tool
work, nor remove the M1 production-isolation and supplier-conformance limits.
