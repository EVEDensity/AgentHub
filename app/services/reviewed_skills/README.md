# Reviewed skill catalog

> Status: implemented, explicit operator injection\
> Owner: Runner and extension maintainers\
> Last reviewed: 2026-10-18\
> Scope: three original text packages and frozen read-only Native tools

This is an explicit operator-selected, read-only catalog, separate from legacy
workspace and user skill discovery. `load_bundled_catalog()` reads only this
Python package's `catalog.json` and `packages/` resources through
`importlib.resources`. `load_catalog(root)` is an operator/test injection point;
never pass a model, webpage or public API supplied root. Neither function installs
or downloads anything, follows home/cwd discovery, or modifies a Contract.

The index pins source URL and exact source commit, version, license, required
capabilities, every file's canonical size/SHA-256 and a complete package digest.
Package content is UTF-8 without BOM/NUL. CRLF and bare CR normalize to LF before
hashing, size checks and freezing; other text bytes are preserved. The package
digest hashes the file-entry list sorted by path, using JSON `sort_keys=True`,
`separators=(',', ':')`, `ensure_ascii=False`, `allow_nan=False`, UTF-8 and no
trailing newline. Digests use the `sha256:` prefix. Unexpected/missing files,
linked paths, conflicting metadata or invalid digests fail the complete load.

All indexed text is verified and frozen in memory at catalog construction. Model
disclosure remains progressive: metadata discovery excludes bodies/resources,
body loading exposes one chosen package, and reference loading is explicit.
Changing the original files later cannot replace an active catalog's content.
This is reviewed release content, not a cryptographic publisher-authentication
claim or protection from an actor able to replace application code and its index.

Supported frontmatter is a deliberately small Agent Skills subset: ASCII name
matching its directory, nonempty description and license, string-valued metadata
with an explicit version, and optional compatibility/allowed-tools strings.
PyYAML SafeLoader is bounded and rejects duplicate keys, aliases, anchors,
unknown fields, non-string metadata and excessive structure. It does not silently
fall back to permissive parsing. Only SKILL.md, LICENSE and one-level Markdown
references are supported; scripts/binary assets and complete standard
compatibility are outside this slice.

`build_reviewed_skill_tools(catalog, available_capabilities=frozenset(...))`
returns exactly `skill_list` and `skill_load`. It never adds granted tools or
permissions. The operator supplies the already available capabilities; the
default empty set grants none. Discovery reports missing capabilities and body/
resource loading refuses packages whose requirements are absent. `allowed-tools`
is informational only. Tools are stateless: `skill_load(name, resource=...)`
explicitly selects one listed reference and does not require an earlier body
load. A normal discovery/body/reference trace progressively discloses content,
without treating a previous read as an authorization. Both tool descriptions include
the frozen catalog digest and a canonical sorted available-capabilities digest so
existing recovery tool fingerprints detect content or admission changes. This
slice does not alter legacy behavior when reviewed mode is absent.

Successful responses are complete JSON. Oversized responses produce a small
explicit error, never a truncated body or partial JSON; `max_result_chars` must
be at least 64. Reading
skill instructions does not establish task success, code correctness or an
independent test verdict.

For an explicit Desktop composition, load the release catalog before building
tools and supply the permitted capability inventory from the composition:

```python
from app.services.desktop_runner_tools import build_desktop_runner_tools
from app.services.reviewed_skills import load_bundled_catalog

tools = build_desktop_runner_tools(
    workspace_root,
    reviewed_catalog=load_bundled_catalog(),
    reviewed_capabilities=permitted_capabilities,  # frozenset, not a new grant
)
```

No default CLI, Web, Desktop or legacy API configuration selects this catalog.
The wheel includes the index and all resources. Frozen desktop executables need
their own resource-collection validation before enabling this mode. See
[ADR-0126](../../../docs/architecture/decisions/0126-frozen-reviewed-skill-catalog.md).
