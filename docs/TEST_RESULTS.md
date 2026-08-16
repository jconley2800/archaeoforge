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

## 2026-08-09 — Babylon spatial-correction and supporting-reference pass

### Root cause reproduced

- The Babylon starter places Etemenanki and the Ishtar Gate only about 242 m apart, while both features
  and their provenance explicitly mark all relative placement as schematic and `needs_review`.
- The compiled preview manifest preserves that uncertainty correctly. The historical-scene wrapper,
  however, unconditionally instructed the image editor to preserve the base render's broad layout and
  relative named-monument positions. The project prompt repeated “preserve its broad relationships.”
- The first researched candidate therefore treated a non-evidentiary placeholder as a fixed anchor;
  the later road-alignment pass changed only the road and stairs and inherited the same compressed
  Gate-Etemenanki relationship.

### Corrected

- The initial correction introduced a broad, evidence-status-derived mutability policy. The later
  Giza stagger regression showed that coupling was unsafe: archaeological uncertainty does not imply
  permission for image generation to regularize or relocate the selected presentation geometry.
  That policy is superseded by the explicit protected-anchor contract documented below.
- Historical requests can now bind up to four ordered supporting references by repeated
  `--reference-image`. Path, role, image index, SHA-256, dimensions, and format are revalidated at
  registration and written into provenance. This closes the prior unbound “Image 2” gap.
- Babylon's canonical prompt now places the single Ishtar Gate in the northern palace zone, directly
  across the Processional Way, with Etemenanki roughly 0.8–0.85 km south-southwest in its own precinct.
  It also treats the river alignment as mutable and specifies the T-shaped southern ziggurat approach.
- Pedersén's Babylon Centre plan crop and an attributed explanatory orientation diagram were retained
  as project assets and hash-bound into the targeted stair refinements.

### Executed and passed

- Full host/live-Blender-aware test collection: 107 tests passed.
- Focused image-finishing tests, including Babylon prompt regression, hash-bound supporting
  references, changed-reference rejection, and precise-mode reference rejection.
- `.venv/bin/ruff check src tests` and `git diff --check`.
- Babylon preview validation: 0 errors, 4 existing `SOURCE_EXTERNAL_ONLY` warnings, 10 eligible features.
- Live built-in GPT Image 2 historical-scene generation from the bound beauty render, followed by
  full-frame visual inspection. Rejected candidates were not published as the final result when they
  duplicated a blue gate or misoriented the ziggurat stairs.
- Registered review-required intermediates preserve the derivation chain from `beauty.png` through the
  spatial correction and plan-assisted stair correction.
- Final registered image: `outputs/renders/babylon-570-bce-stair-rotation-v11.png`, SHA-256
  `27cd46bd9f7078a8f64721871d9a3c9c478e72035a5c9a0f8124d7fcb9b3cc45`, inspected at 1536 x 864.
  The provider's 1672 x 941 same-aspect candidate was explicitly normalized with Lanczos; provenance
  records the transform and therefore keeps manual review required.

### Review status and limits

- No reviewer, acceptance recommendation, or evidence approval was recorded. The final and its
  intermediates remain non-authoritative and `manual_review_required: true`.
- Strict geometry audit was intentionally skipped because it is inapplicable to `historical_scene`.
- The deliberately schematic GeoJSON and Blender beauty render were not altered or re-rendered. The
  correction belongs to the non-authoritative presentation layer; georeferencing the starter would
  violate its evidence-control purpose.
- The final visibly separates the single northern Ishtar Gate from Etemenanki and aligns the gate with
  the road. The low, facade-hugging lateral stair pair reads as the plan's T crossbar, but precise
  stair elevations, parapets, ziggurat upper stages, and summit treatment remain interpretive and need
  a named historical-plausibility review.

## 2026-08-10 — Giza waterfront correction

### Corrected

- Added the 2024 peer-reviewed Ahramat Branch/Giza Inlet study and AERA's 2013–2014
  excavated-basin field report to the Giza preview source catalog.
- Added four `needs_review` waterfront claims, refined the existing PNAS Khufu-branch claim,
  and kept exact 2500 BCE shorelines, channel widths, depths, and docking arrangements explicit
  uncertainties.
- Added a connected Giza-inlet implementation proxy and three non-identical valley-temple water-edge
  guides. A first highly concave polygon rendered as fragmented patches, so the live-rendered guide
  was replaced with stable line-width and convex-polygon proxies without strengthening the claim.
- Replaced the historical-scene instruction that pushed water beyond the crop with an explicit visible
  waterfront: dry valley temples beside restrained inlet and harbor-basin water, a dry Sphinx enclosure,
  and the wider branch and floodplain beyond.

### Executed and passed

- Preserved the prior generated SQLite database as
  `.archaeoforge/project.sqlite3.pre-waterfront-20260810`, rebuilt the active database from the updated
  CSV inputs, and imported 18 sources and 20 claims.
- Preview validation: 0 errors, 17 expected `SOURCE_EXTERNAL_ONLY` warnings, 27 eligible features.
- Preview compilation: 27 compiled features, 20 claims, 18 sources; input fingerprint
  `78f5e769d38c8f21a283ce120d8111cc8091e4958d6a0572bc5854927920c269`.
- Live Blender 5.2 render completed with beauty, depth, diffuse, normal, and Cryptomatte outputs;
  the render log recorded the auto-framed camera and no ArchaeoForge error.
- Prepared and hash-checked historical-scene request
  `image_finish_request_waterfront_v2.json`, binding the fresh beauty render, current preview manifest,
  revised prompt, and the earlier finish as a supporting reference.
- Generated and visually inspected the full-frame built-in GPT Image 2 candidate. It retains exactly
  three main pyramids, the subsidiary groups, covered causeways, valley temples, and a dry Sphinx
  enclosure while bringing connected water to the lower temple fronts.
- Registration correctly rejected the provider's native 1672 by 941 dimensions. Compatible 16:9
  normalization was then explicitly requested and recorded as Lanczos 1536 by 864.
- Final registered image: `outputs/renders/giza-2500-bce-waterfront-v2.png`, SHA-256
  `dbd8d113304f983a2440abc0c35d527b28e1ceee9fce2b8bbf7bfb3b33a45331`.

### Review status and limits

- The project remains a preview with draft/needs-review evidence and external-source warnings.
- The registered image is a non-authoritative derived presentation layer. No named reviewer,
  recommendation, evidence approval, or strict geometry audit was recorded; manual historical-
  plausibility review remains required.
- The visible bank line, channel width, quay treatment, boat placement, seasonal water level, and
  simultaneous pristine completion remain interpretive.

## 2026-08-10 — Giza evidence-led late Fourth Dynasty pass

### Corrected and enriched

- Replaced the simultaneous all-white completion treatment with a coherent approximately 2500 BCE
  transition under the Egyptian Ministry chronology: maintained Khufu and Khafre complexes alongside
  Menkaure's granite lower casing, rough unfinished blocks, and whitewashed-mudbrick completion.
- Added source-bound distinctions for Khafre's granite basal band, valley-temple materials and plain
  covered causeway; Khufu's basalt temple pavement and relief fabric; Menkaure's subsidiary forms;
  the unfinished Sphinx Temple; the early Eastern and Western cemeteries; Heit el-Ghurab; the Wall of
  the Crow; and restrained royal-cult activity.
- Added approximate, explicitly proxy-only guide geometry for the two early mastaba fields, the final-
  phase workers' settlement, Wall of the Crow, and Sphinx Temple. The final presentation also includes
  hard limestone quarry terraces, a narrow floodplain, sparse boats, people, animals, and settlement
  activity where direct evidence or conservative Fourth Dynasty comparanda support them.
- Corrected the rendered casing from decorative random crack patterns to tight horizontal courses,
  reduced the waterfront from a dark marine bay to a calmer silty inlet, and separated Menkaure's
  causeway from the workers' settlement wall.

### Executed and passed

- Preserved the preceding database as `.archaeoforge/project.sqlite3.pre-history-20260810`, rebuilt
  the active database from CSV, and imported 27 sources and 33 claims.
- Preview validation: 0 errors, 26 expected `SOURCE_EXTERNAL_ONLY` warnings, 32 eligible features.
- Preview compilation: 32 compiled features, 33 claims, 27 sources; input fingerprint
  `29473dd5d40a48c382b0fe708a98b0413bcdc0584f6ec579e656b37ed4c20bde`.
- Live Blender 5.2 render completed with 1536 by 864 beauty, depth, diffuse, normal, and Cryptomatte
  outputs. The evidence register was explicitly re-exported and contains all 33 claims, including the
  previously omitted hydrology claims.
- Prepared versioned hash-bound request `image_finish_request_historical_v3.json`, generated three
  versioned candidates, inspected each full frame, and retained the earlier waterfront finish only as
  a bound supporting reference.
- Registration correctly rejected the provider's 1672 by 941 dimensions; explicit compatible Lanczos
  normalization produced the 1536 by 864 registered output.
- Final registered image: `outputs/renders/giza-2500-bce-historical-v3.png`, SHA-256
  `c6f2bb18c085438b14167c3f6c8df7d053c7d55db1b0f22554854e6ec3db875f`.

### Review status and limits

- The result remains a preview. All new archaeological claims are `needs_review`, external web sources
  have no local immutable copies, and exact chronology, causeway roofs, shoreline, settlement phase,
  Sphinx appearance, boats, people, crops, and local coordinates remain explicit uncertainties.
- The registered image is a non-authoritative presentation layer. No named reviewer, acceptance
  recommendation, evidence approval, or strict geometry audit was recorded; provenance keeps
  `manual_review_required: true`.

## 2026-08-10 — historical protected-anchor contract and Giza stagger gate

### Root cause reproduced

- The compiled Giza manifest already placed the three principal pyramid centers at distinct
  northeast-to-southwest offsets. The image-finishing wrapper nevertheless coupled preview evidence
  status to presentation mutability, so the generator could regularize those selected positions.
- `historical_scene` correctly skipped the strict pixel/geometry-preservation audit, but had no
  narrower validation for protected spatial relationships. `manual_review_required: true` was
  provenance metadata rather than a publication gate, so a visually plausible image with a collapsed
  pyramid stagger could still reach `outputs/renders`.

### Corrected

- Historical finishing now requires a project-relative
  `ai.historical_scene_spatial_contract`. Contract schema 1 names required `presence`,
  `relative_layout`, `visible_stagger`, `topology`, `orientation`, and `scale_hierarchy`
  relationships by stable constraint and manifest feature IDs. Evidence review status remains
  independent of presentation-anchor protection.
- Finish request schema 4 hash-binds the contract, semantic base-render requirements, successful
  render receipt, and each protected feature's manifest template, geometry, parameters, review
  status, and evidence IDs. Contract, receipt, beauty-image, template-semantic, or snapshot changes
  make the request stale. Legacy historical schemas 1 through 3 cannot publish without preparing a
  new receipt-bound request.
- Historical prompts now begin with a `NON-NEGOTIABLE SPATIAL CONTRACT`. Proxy form, materials,
  inhabitants, vegetation, and surface finish may change; protected placement, ordering, topology,
  orientation, scale relationship, viewpoint, and crop may not. Supporting references cannot
  override the contract.
- Registration and the unattended API lane use a dedicated historical protected-anchor assessment,
  separate from the inapplicable strict geometry audit. It must return every required constraint ID
  exactly once, preserve viewpoint and crop, retain every protected feature, meet the confidence
  threshold, and recommend acceptance.
- A missing, incomplete, low-confidence, review, reject, or failed protected-anchor assessment now
  stops before the canonical PNG or provenance sidecar is written. Interactive registration reports
  `validation_blocked` and exits 2. A named `--spatial-recommendation accept` may substitute only
  when automatic assessment is unavailable and covers the complete contract; a general
  historical-plausibility acceptance cannot override the spatial gate.
- Giza's contract explicitly protects the three principal pyramids as distinct features and requires
  their selected Khufu-Khafre-Menkaure northeast-to-southwest stagger. The base scene now uses the
  native planar `pyramid` template with limestone and granite materials, including separate lower
  casing treatment where the selected reconstruction calls for it.

### Regression coverage

- Focused tests cover contract requirement and validation, binding of preview/`needs_review`
  protected features, frontloaded prompt constraints, stale-contract rejection, exact assessment
  coverage, low-confidence and failed-check rejection, named fallback review, publication ordering,
  CLI exit 2, and the unattended historical API lane.
- Project-level regression checks verify that the Giza contract binds all three principal pyramid
  feature snapshots and contains the required visible-stagger relationship.

## 2026-08-10 — semantic evidence render, Sphinx correction, and receipt gate

### Corrected

- Added native planar `pyramid` and identity-specific `sphinx` Blender templates so critical
  landmarks begin image generation as recognizable evidence geometry rather than generic boxes or
  stepped stand-ins. The Sphinx proxy contains one attached head, chest, continuous low lion body
  with integrated hindquarters, paired forepaws, and an explicit east-facing direction.
- Added conservative template recognizability classes and per-feature
  `base_render_requirements`. A historical request now fails before generation when a protected
  critical landmark is still only a generic envelope or does not meet its declared semantic level.
- Added schema-1 `blender_result.json`. The schema-4 finish request and final provenance bind its
  manifest hash and input fingerprint, beauty-image bytes and frame, and complete feature-template
  semantic table. Historical Image 1 must be that fresh `beauty.png`; prior generated images remain
  supporting references only.
- Expanded Giza's contract to six checks covering the surveyed pyramid centers and visible stagger,
  one complete Sphinx, east-facing orientation, scale hierarchy, and dry enclosure/causeway/temple/
  water topology. A malformed earlier candidate was rejected with `validation_blocked` before
  publication.

### Executed and passed

- Preview validation completed with 0 errors, 26 expected `SOURCE_EXTERNAL_ONLY` warnings, and
  32/32 eligible features. The manifest contains 35 claims and 27 sources.
- Blender 5.2 produced the 1536 by 864 beauty render plus depth, diffuse, normal, and Cryptomatte
  passes. The receipt classifies the three main pyramids as `type_specific` and the Great Sphinx as
  `identity_specific`.
- The accepted candidate visually satisfies all six protected constraints. Compatible Lanczos
  normalization from 1672 by 941 to 1536 by 864 is recorded. Final image:
  `outputs/renders/giza-2500-bce-historical-sphinx-v7.png`, SHA-256
  `da62a32cb533e22cb804f124c7ea4a244933d6edee37c312e944d785994aa5c9`.
- The complete repository suite passes: 132 tests, Ruff, bytecode compilation, and
  `git diff --check`.

### Review status and limits

- Automatic protected-anchor assessment was unavailable because `OPENAI_API_KEY` was unset. A
  named manual review accepted only the complete visual spatial contract; provenance remains
  `registered_review_required`, with historical plausibility set to `review`.
- The project remains a preview. Its 26 archaeological sources are external-only rather than local
  immutable snapshots, exact metric dimensions cannot be proven from raster pixels, and the visible
  Sphinx enclosure remains a restrained schematic quarry setting rather than surveyed ditch
  geometry.
