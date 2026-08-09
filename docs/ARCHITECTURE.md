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
  -> drift audit and explicit provenance record
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

Blender's Python API is the moving part in this pipeline. Engine identifiers, compositor node types, colour-management look names, and world shading have all changed across recent releases, and the failure mode is silence rather than a crash: an ignored assignment still produces a valid PNG. The script therefore probes what the installed build offers and prints an `ARCHAEOFORGE WARNING` line when a requested setting is unavailable.

### `image_finish.py`

Maintains two intentionally separate finishing lanes. The public OpenAI API lane can run
unattended. The interactive lane writes a project-relative request bound to the base-image,
prompt, and manifest hashes; Codex or another interactive editor consumes it; registration
then validates the returned image and writes a non-authoritative provenance sidecar. Codex's
built-in image tool is never represented as an in-process Python provider. An optional
structured geometry audit and an explicit manual recommendation can mark a result accept,
review, or reject without changing the authority of the evidence register or scene manifest.
The bound `finish_mode` distinguishes a strict `precise_object_edit`, where the base render is
the authoritative spatial constraint, from `historical_scene`, where a schematic render is only
a broad compositional guide for a lifelike interpretation. The strict geometry audit is
inapplicable to historical-scene generation and is bound off in its request. A named,
unnormalized `historical_plausibility` acceptance may clear the review-required flag, but it cannot
masquerade as a geometry-preserving edit or change the image's non-authoritative status. Finish
request and record schema 2 carry the mode explicitly; legacy request schema 1 is accepted only as
the original mode-less `precise_object_edit` form.
The editor writes a candidate, never the canonical final path; registration alone publishes the
PNG/provenance/audit set under `outputs/renders`. Hashes provide stale-content and accidental-edit
integrity, not authenticity against a process that can rewrite the request and recompute them.
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
