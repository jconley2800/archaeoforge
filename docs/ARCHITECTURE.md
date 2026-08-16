# Architecture

## Trust boundary

ArchaeoForge treats research data and visual presentation as separate layers.

```text
Source files
  -> immutable hashes and indexed pages
  -> proposed evidence claims
  -> human review and audit trail
  -> dated GeoJSON features linked to claims
  -> validation gate
  -> deterministic scene manifest
  -> Blender scene and render passes
  -> optional API finishing or hash-bound interactive-image handoff
  -> mode-appropriate drift or protected-anchor audit
  -> explicit provenance record
```

Only the evidence register, reviewed geometry, and scene manifest are authoritative. Rendered pixels are derived products.

## Components

### `ingest.py`

Discovers project sources, reads sidecars, hashes files, extracts text or metadata, and writes source pages to SQLite. Failures are isolated per file.

### `extract.py`

Uses structured model output to propose claims. Direct PDF inputs retain page text and page imagery. Images are supplied natively. Other documents use bounded text windows. AI claims are not approved automatically.

### `db.py`

Provides the provenance database, stable claim fingerprints, source-version snapshots, source catalog import, evidence seed import, CSV export, and append-only review records.

### `validate.py`

Checks source path confinement, file existence, hashes, claim-source bindings, required locators, quotations, date ranges, review status, geometry validity, duplicate geometry, alternative-hypothesis conflicts, and suspicious water-solid overlap.

### `compile_scene.py`

Applies policy again during compilation. This defense-in-depth design means direct use of the compiler still rejects blocked claims and changed sources. It calculates the weakest evidence class and lowest confidence across each feature's evidence chain.

### `georef.py`

Builds reproducible GDAL commands from a reviewed GCP table. It does not choose control points.

### `framing.py`

Solves camera placement from a bounding box using the standard library only, so the same code runs inside Blender's Python and under the host test suite. The distance is derived from the eight corners of the compiled scene bounds rather than estimated from a radius, which keeps a wide flat site framed as tightly as a compact one.

### `blender/build_scene.py`

Runs inside Blender without third-party Python packages. It creates deterministic metric geometry from the manifest, assigns diagnostic or realistic materials, frames the camera through `framing.py`, builds a node-based sky world for ambient fill, aims the sun from compass angles, writes render passes, embeds provenance, and saves the `.blend` file. Line features use shared pure-Python miter geometry so adjacent wall and road segments abut instead of overlapping.

Native `pyramid` and `sphinx` templates carry recognizable semantic form into the evidence render
before image generation begins. Each built object records its source template and the conservative
template class `generic_envelope`, `type_specific`, or `identity_specific`; unknown templates remain
generic rather than receiving an optimistic classification. The Sphinx proxy encodes one continuous
east-facing head, chest, recumbent lion body, hindquarters, and paired forepaws while leaving carving
detail interpretive.

Blender's Python API is the moving part in this pipeline. Engine identifiers, compositor node types, colour-management look names, and world shading have all changed across recent releases, and the failure mode is silence rather than a crash: an ignored assignment still produces a valid PNG. The script therefore probes what the installed build offers and prints an `ARCHAEOFORGE WARNING` line when a requested setting is unavailable.

### `blender_runner.py`

After a successful render, the host runner writes schema-1
`outputs/exports/blender_result.json`. This render receipt binds the compiled-manifest path, SHA-256,
and input fingerprint; every feature's template and recognizability class; and the exact beauty-image
path, SHA-256, dimensions, and format. A build-only result does not claim a completed render. The
receipt therefore closes the gap between “the current manifest is suitable” and “Image 1 was
actually rendered from that manifest.”

### `image_finish.py`

Maintains two intentionally separate finishing lanes. The public OpenAI API lane can run
unattended. The interactive lane writes a project-relative request bound to the base-image,
prompt, and manifest hashes; Codex or another interactive editor consumes it; registration
then validates the returned image and writes a non-authoritative provenance sidecar. Codex's
built-in image tool is never represented as an in-process Python provider. An optional
structured geometry audit and an explicit manual recommendation can mark a result accept,
review, or reject without changing the authority of the evidence register or scene manifest.
The bound `finish_mode` distinguishes a strict `precise_object_edit`, where the base render is
the authoritative spatial constraint, from `historical_scene`, where a project-authored spatial
contract protects selected manifest features and their required visible relationships while
allowing proxy form and surface treatment to change. Evidence review status and presentation-anchor
protection are separate dimensions: `needs_review` describes archaeological confidence, not
permission for image generation to relocate a selected feature.

Historical-scene preparation accepts only the current `outputs/renders/beauty.png` as Image 1 and
requires its matching render receipt. Contract `base_render_requirements` may set a per-feature
minimum of `type_specific` or `identity_specific`; a protected critical landmark whose compiled
template is unknown or too generic blocks before generation. Prior candidates, registered finishes,
plans, and comparison images are supporting references from Image 2 onward and can never become the
spatial base.

The strict pixel/geometry-preservation audit is inapplicable to historical-scene generation and is
bound off in its request. Instead, a historical protected-anchor audit compares the bound base and
candidate against every required contract constraint. It must return the exact constraint set,
preserve viewpoint and crop, retain every protected feature, meet the acceptance-confidence
threshold, and recommend acceptance. Missing, incomplete, uncertain, or failed validation stops
before the canonical PNG is copied. If the automatic assessment cannot run, a real named reviewer
may supply `--spatial-recommendation accept` for the complete contract; a general
`historical_plausibility` recommendation cannot substitute. The CLI exposes a stopped gate as
`validation_blocked` with exit status 2, and the unattended API lane applies the same fail-closed
rule.

Finish request schema 4 binds the mode, spatial-contract path and hash, semantic base-render
requirements, protected manifest-feature snapshots, and the schema-1 render receipt. Historical
requests using schemas 1 through 3 are rejected because they do not bind the complete current
receipt workflow; those schemas remain readable only for their compatible legacy precise-edit
forms. New provenance uses finish record schema 3, records the bound receipt, and keeps the general
plausibility review separate from historical spatial validation.
The editor writes a candidate, never the canonical final path; registration alone publishes the
PNG/provenance/audit set under `outputs/renders`. Historical requests may also carry ordered,
role-labelled supporting reference images, each bound by project-relative path and image metadata;
registration revalidates them and records them in provenance. A prior generated image is valid only
in that supporting role. Hashes provide stale-content and
accidental-edit integrity, not authenticity against a process that can rewrite the request and recompute them.
API-backed paths read only the project API key and pin the SDK to the official OpenAI endpoint.

### `report.py`

Creates an autoescaped standalone HTML review packet from the database, validation report,
and scene manifest. ChatGPT handoffs recursively redact absolute local paths while retaining
project-relative references and public URLs.

## Project state

```text
project/
├── project.yaml
├── sources/
├── data/
│   ├── source_catalog.csv
│   ├── evidence_seed.csv
│   ├── features.geojson
│   ├── historical_scene_spatial_contract.json
│   └── gcps.csv
├── assets/
├── prompts/
├── .archaeoforge/
│   ├── project.sqlite3
│   ├── cache/
│   └── logs/
└── outputs/
```

`.archaeoforge` and `outputs` are reproducible working state and generated outputs. The source tree, project configuration, feature geometry, evidence seed, and review database should be versioned according to the project governance model.

## Authoritative-mode invariants

A feature is included only when:

1. Its feature status is allowed.
2. Its date range includes the target year.
3. Its geometry is valid and nonempty.
4. Required evidence IDs exist.
5. Every linked claim is allowed.
6. Every linked claim applies to the target year.
7. Mutually exclusive alternatives are not combined.
8. Every linked claim is bound to a source checksum.
9. The source bytes still match that checksum.

A failed invariant excludes the feature. Validation errors stop the orchestrated run before Blender is invoked.
