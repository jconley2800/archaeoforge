# Verification record

## 2026-08-09 — completion and Codex image-generation integration

### Passed

- 85 regression tests, including the live Blender 5.2 integration tests
- Babylon preview orchestration: 10 compiled features, zero validation errors
- A fresh Blender 5.2.0 LTS Babylon build and render with beauty, depth, diffuse, normal, and
  cryptomatte outputs
- Dense, stable object-index assignment for all 10 scene features and export of
  `object_index_map.json`
- A clean repository-wide `ruff check .` and `git diff --check`
- A complete source distribution containing the documentation, starter project, strict Codex prompt,
  tests, and shell entry points while excluding generated project state; the wheel contains the
  installable runtime package, report template, metadata, license, and console entry point

### Fixed

- Object IDs no longer saturate at Blender's maximum pass index. Blender versions without `IndexOB`
  now receive a correctly named cryptomatte output instead of a misleading `object_index.exr`.
- Wall and road corners use bounded miter joins instead of overlapping centered boxes.
- HTML report values are escaped and ChatGPT handoffs recursively redact local absolute paths.
- Unknown project configuration keys fail validation, and project initialization cannot overwrite
  scaffold files without both explicit overwrite flags.
- Malformed JSON-list fields and geometry-overlap failures can no longer disappear through broad
  exception handling.
- `ai.finish_enabled` is wired into orchestration, with explicit skip and pending-external states.
- The image-finishing workflow now separates the public OpenAI API path from the Codex interactive
  handoff, verifies content hashes and dimensions, writes atomically, and records provenance.
- Geometry audit selection respects configuration unless the user explicitly passes `--audit` or
  `--no-audit`.
- Finish requests and published images are confined to their output directories; image, provenance,
  and audit sidecars are collision-checked as one set, API bytes are staged and validated before
  publication, stale audits are cleared on forced replacement, and long-running calls revalidate
  their base/manifest/request bindings before publication.
- The OpenAI client reads only the selected project's API key and is pinned to the official endpoint;
  GPT Image 2 size constraints and high input fidelity are applied before generation, and URL-based
  image responses are rejected rather than fetched.
- A stale beauty render is not finished when Blender did not complete, contradictory audit output
  cannot approve geometry, normalized results remain review-required, and initialization rejects
  symlink escapes even with overwrite flags.

### Image-generation verification

- `prepare-finish` produced a project-relative, hash-bound request for the final Babylon beauty render.
- Codex's built-in image generator consumed the request and produced a real finishing candidate at a
  separate tool-managed path; only the registration gate published the review artifact.
- The candidate changed the camera framing and arrived at 1672 x 941 rather than the requested
  1536 x 864. It was therefore normalized only for inspection, registered as
  `finished-review-required.png`, and given a manual `reject` recommendation instead of being promoted.
- The provenance sidecar binds the request, original render, prompt, normalized candidate, dimensions,
  reviewer decision, and reason. This exercises the fail-closed review boundary.

### Not executed in this environment

- A live public OpenAI API finish or geometry-audit request; those paths were covered with mocked API
  tests because no API key was configured
- GDAL raster transformation; `gdal_translate` and `gdalwarp` are not installed

## 2026-08-09 — lifelike historical-scene mode and live Babylon run

### Passed

- 102 regression tests, including 54 focused image-finishing tests, 9 safety tests, and the live
  Blender integration path
- Repository-wide `ruff check .`, targeted Ruff formatting checks for the finish-mode changes, and
  `git diff --check`
- A fresh `run_babylon_demo.sh` execution through source catalog import, ingestion, validation,
  preview compilation, report/export generation, Blender 5.2 scene build, and 1536 x 864 beauty render
- Explicit `historical_scene` request preparation through the configured conventional prompt at
  `prompts/finish_historical_scene.txt`
- A live Codex built-in image-generation call using the exact hash-bound request prompt and the fresh
  Blender render as a broad spatial/compositional reference
- Registration of the tool-returned 1672 x 941 PNG through the publication gate, including same-aspect
  normalization to 1536 x 864 and verified output SHA-256

### Historical-scene behavior verified

- `finish_mode` is bound into the request ID and copied into provenance. The default remains the strict
  `precise_object_edit`; an unknown mode fails configuration validation.
- Historical mode does not inherit the strict material-pass prompt, pixel-lock language, or geometry
  audit. It records why that audit is inapplicable; only a named, unnormalized historical-plausibility
  acceptance may clear review-required status.
- The final derived image is `outputs/renders/babylon-570-bce-final.png`, SHA-256
  `6910b3cb08404878073443ec188d9a73d1dcf2fc6624e9eec5671e79e2d2fab9`.
- Its provenance record binds request
  `99219f6473781b9c7a45103060caed91335e24b8240e02948c3d63bd0a9264db`, the source beauty render,
  scene manifest, exact prompt, original tool artifact, normalization, and a manual `review`
  recommendation. It remains an interpretive presentation image, not archaeological evidence.

### Not executed in this run

- A public OpenAI Image API call or Responses API geometry audit; Codex's built-in image generator was
  used directly, and strict geometry auditing is intentionally inapplicable to `historical_scene`
- GDAL raster transformation; `gdal_translate` and `gdalwarp` are not installed

## 2026-08-09 — render pipeline repair

### Passed

- 28 regression tests, including 11 new camera-framing tests and 4 new Blender integration tests
- Babylon preview orchestration end to end, unchanged: 10 compiled features, zero validation errors
- Live Blender 5.2.0 LTS build and render of the Babylon starter, including all four render passes
- `ruff check` clean on the files touched by this pass

### Fixed

- Camera far clipping was never set, so Blender's factory 1000 m far plane deleted the far half of the
  site. The 700 x 850 m terrain rectangle rendered as a truncated wedge. This was the root cause of the
  reported bad render.
- The camera is now solved from the compiled scene bounds instead of hand-placed coordinates.
- `scene.world.color` was being set on a node-based world, which Blender ignores. The background was
  therefore the default grey and there was no ambient fill. The world is now a real sky node tree.
- The colour-management look was assigned inside a bare `except`, so an unavailable name was silently
  dropped. Blender 5.2 offers no AgX looks at all, so the requested look never applied.
- `samples` was only honoured under Cycles; EEVEE ran at its own default.
- EEVEE ray tracing was left off, so there was no ambient occlusion or indirect bounce.
- Render passes were written as `depth_.exr` rather than the documented `depth.exr`.
- Ziggurat stair positions were computed in unrotated space while each step box was rotated on its own
  axis, so a rotated ziggurat left its staircase behind on the unrotated south face.
- `engine: BLENDER_EEVEE_NEXT` is not a valid identifier on Blender 5.2; the resolver already handled it
  but the shipped config was stale.
- The committed example outputs leaked an absolute home directory path.

### Known and not yet fixed

- `Object.pass_index` saturates at 32767 for every object, so the object-index pass cannot distinguish
  features. Blender 5.2's Render Layers node also has no `IndexOB` socket, so `object_index.exr`
  currently holds a cryptomatte layer under the wrong name.
- Wall and road segment boxes are not mitred at corners, so a corner overlaps rather than joins.
- `report.html.j2` is not autoescaped: `select_autoescape(["html", "xml"])` splits on the last extension
  and never matches a `.j2` filename.
- `chatgpt_handoff.json` strips `project_root` but still carries absolute paths from source records.
- `ai.finish_enabled` is declared but never read.
- The sdist omits `tests/conftest.py`, `projects/`, `docs/`, and the shell scripts.
- Pydantic config models do not set `extra="forbid"`, so a mistyped key in `project.yaml` is ignored.
- `archaeoforge init` overwrites an existing project without confirmation.
- The working tree is not under version control.

### Not executed in this environment

- GDAL raster transformation: `gdal_translate` and `gdalwarp` are not installed
- OpenAI extraction, image finishing, and geometry audit network calls: no API key was configured

## 2026-08-08 — initial verification

### Passed

- Python syntax compilation for all host and Blender-side scripts
- 13 regression tests
- Clean Babylon preview orchestration through source catalog import, ingestion, claim import, validation, manifest compilation, evidence CSV export, and HTML report generation
- Authoritative Babylon run correctly stopped with validation errors because starter features and claims are not approved and several sources are external-only
- Python wheel build with package templates and Blender script included
- CLI help, project initialization, empty-project orchestration, status, and environment diagnostics
- Linux shell syntax for installation and demonstration scripts

### Regression coverage

- Feature and claim review gates
- Preview versus authoritative policy
- Target-date applicability
- Stable input fingerprints
- Append-only claim review audit
- Source-byte mutation invalidation
- Text ingestion and SHA-256 indexing
- GDAL command generation
- Minimum GCP requirements
- Source catalog merge safety
- AI Class A downgrade pending human review
- Historical year-zero rejection
- Babylon starter end-to-end preview

### Not executed in that build environment

- Blender scene construction and rendering, because the Blender binary was not installed
- GDAL raster transformation, because `gdal_translate` and `gdalwarp` were not installed
- OpenAI extraction, image finishing, and geometry audit network calls, because no API key was configured

These limitations are stated so the verification record does not imply execution that did not occur.

## 2026-08-09 — finish API and provenance post-review hardening

### Passed

- 102 regression tests, including 54 focused image-finishing tests and 9 safety tests
- Repository-wide `ruff check .`, targeted Ruff formatting checks, and targeted `git diff --check`
- GPT Image 2 request mocks at the valid 1024 x 640 minimum pixel budget
- Finish request schema 2 mode validation plus compatibility tests for mode-less schema 1 requests
- Scoped `geometry_preservation` and `historical_plausibility` manual-review provenance

### Corrected

- GPT Image 2 edits omit the unsupported `input_fidelity` request parameter and record its effective
  automatic high-fidelity behavior as `automatic_high`; the retained config field accepts only `high`.
- Public API preflight enforces the documented 655,360-pixel minimum in addition to divisibility,
  aspect-ratio, edge-length, and maximum-pixel constraints.
- Historical-scene requests bind strict geometry auditing off, explicit audit enablement is rejected,
  and only a named, unnormalized historical-plausibility acceptance may clear review-required status.
- Generic historical fallback prompts interpolate the selected project's place and target-date label.

### Not executed in this post-review pass

- A live public OpenAI Image API call or Responses API geometry audit; those paths remained covered by
  mocked API tests because no network call was required for these contract and provenance corrections.
