# ArchaeoForge

ArchaeoForge is a Linux-first, evidence-controlled pipeline for reconstructing lost places from archaeological plans, excavation reports, survey data, museum records, ancient texts, and reviewed comparative evidence.

It is designed to prevent a common failure mode in historical visualization: a convincing image that silently mixes measurement, scholarly reconstruction, analogy, and invention.

The authoritative artifact is not the final image. It is a versioned evidence register plus a deterministic scene manifest. Blender, Unreal, still renders, and AI finishing are downstream presentation layers.

## What the pipeline automates

1. Catalogues and hashes local source files.
2. Extracts page text, page images, workbook contents, image metadata, and structured file contents.
3. Optionally proposes structured evidence claims with a multimodal OpenAI model.
4. Forces all AI-generated claims into human review and prevents AI from assigning final Class A status.
5. Records claim status changes in an append-only review audit trail.
6. Validates chronology, source checksums, locators, quotations, feature geometry, evidence links, and mutually exclusive hypotheses.
7. Generates GDAL georeferencing commands from human-selected ground control points.
8. Compiles approved GeoJSON features and evidence into a deterministic scene manifest.
9. Builds a metric Blender scene with procedural templates and provenance embedded in every object.
10. Renders beauty, depth, normal, diffuse, and object-index passes.
11. Optionally uses GPT Image for a constrained finishing pass, followed by a multimodal geometry-drift audit.
12. Produces an evidence register CSV and standalone HTML review report.

## What it deliberately does not automate

ArchaeoForge does not decide which sources are credible, select ground control points without human input, resolve disputed reconstructions, approve AI-extracted claims, infer missing buildings as fact, or guarantee that an attractive render is historically correct.

A source can be ingested automatically. Its interpretation still requires an archaeologist, historian, architect, epigrapher, survey specialist, or other relevant reviewer.

## Evidence classes

| Class | Meaning | Typical examples |
|---|---|---|
| A | Directly measured or observed | Surveyed foundation, scan, measured artifact, in-situ wall |
| B | Strongly constrained reconstruction | Missing elevation derived from repeated fragments, dimensions, and structural relationships |
| C | Comparative inference | House elevation based on a securely dated regional parallel |
| D | Cinematic completion | Incidental people, dust, cloth movement, transient clutter |

By default, an authoritative build includes only approved features whose linked claims are also approved and bound to the exact source checksum reviewed. Preview mode can include draft and needs-review material, but marks it as preview output.

## Quick installation on Linux

Requirements:

- Python 3.11 or newer
- Blender for scene generation and rendering
- GDAL or QGIS for raster georeferencing
- An OpenAI API key only for optional AI extraction and finishing

Run:

```bash
cd archaeoforge
./install_linux.sh
source .venv/bin/activate
archaeoforge doctor projects/babylon_570_bce
```

On Debian, Ubuntu, or Linux Mint, GDAL and QGIS can normally be installed through the distribution package manager:

```bash
sudo apt update
sudo apt install gdal-bin qgis
```

Install Blender through your preferred package source, then set `blender.executable` in `project.yaml` if the binary is not named `blender` or is outside `PATH`.

## Install the Codex plugin

The repository also contains the ArchaeoForge skills-only Codex plugin. After cloning the public repository and installing the CLI dependencies above, add its marketplace and install the plugin:

```bash
codex plugin marketplace add jconley2800/archaeoforge
codex plugin add archaeoforge@archaeoforge
```

Start a new Codex conversation, then ask it to use `$archaeoforge-reconstruction`. The supported end-to-end workflow is currently Linux-first and requires Codex; the plugin does not provide a standalone graphical application.

## Run the Babylon starter

```bash
./run_babylon_demo.sh
```

Equivalent command:

```bash
archaeoforge run projects/babylon_570_bce --preview --skip-ai
```

The orchestrator automatically skips Blender if it is not installed. To exercise only the evidence, validation, manifest, and report stages:

```bash
archaeoforge run projects/babylon_570_bce --preview --skip-ai --skip-blender
```

Outputs are written under:

```text
projects/babylon_570_bce/outputs/
├── exports/
│   ├── chatgpt_handoff.json
│   ├── evidence_register.csv
│   ├── image_finish_request.json
│   ├── object_index_map.json
│   └── scene_manifest.json
├── reports/
│   ├── index.html
│   └── validation.json
├── renders/
│   ├── beauty.png
│   ├── finished.png
│   ├── finished.provenance.json
│   └── passes/
│       ├── depth.exr
│       ├── diffuse.exr
│       ├── normal.exr
│       └── cryptomatte_object.exr
└── babylon_570_bce.blend
```

Blender versions that expose the compositor's `IndexOB` socket also write
`object_index.exr`. Blender 5.2 exposes object cryptomatte instead, so it writes
`cryptomatte_object.exr`. `object_index_map.json` provides the deterministic dense
feature-ID mapping used by object pass indices and embedded in the `.blend` scene.

The included Babylon geometry is intentionally schematic. It demonstrates pipeline behavior, not a finished scholarly reconstruction. The starter refuses an authoritative build because its evidence and features remain `needs_review` and several bibliographic records do not yet have immutable local source copies.

## Feed a result into ChatGPT

Every successful `archaeoforge run` writes `outputs/exports/chatgpt_handoff.json`. Upload that single file to ChatGPT and use the `suggested_prompt` value contained in it. The handoff includes the project configuration, sources, evidence claims, review history, validation result, and scene manifest without absolute local paths.

To regenerate only the handoff from the current project state:

```bash
archaeoforge export-chatgpt projects/babylon_570_bce
```

## Create a project

```bash
archaeoforge init projects/my_site \
  --title "My Site, approximately 300 BCE" \
  --place "My Site" \
  --year -300 \
  --label "approximately 300 BCE"
```

`init` will not replace existing scaffold files. `--force` may add missing scaffold to a
non-empty directory; replacing `project.yaml`, catalogues, features, or the finish prompt
requires the deliberately explicit `--force --overwrite-existing` combination.

BCE years are negative. CE years are positive. Year zero is rejected.

## Add sources

Place permitted source files under `sources/`. Supported formats include PDF, DOCX, XLSX, CSV, text, Markdown, JSON, GeoJSON, YAML, common raster image formats, OBJ, PLY, GLB, and glTF.

A source sidecar controls its stable ID and metadata:

```yaml
# sources/excavation_report.pdf.source.yaml
id: SRC-EXCAVATION-1925
title: Excavations at the Site
authors: A. Researcher
publication_year: 1925
source_type: excavation_report
license: Public domain
notes: Scanned edition used for page-level review.
```

Then run:

```bash
archaeoforge ingest projects/my_site --render-visual-pages
```

Every local source is SHA-256 hashed. Claims retain the source checksum present when the claim was created. A changed source invalidates dependent geometry until it is reviewed again.

## Optional multimodal evidence extraction

Copy `.env.example` into the selected project, add your API key, and set `ai.enabled: true` in that project's configuration.

```bash
cp .env.example projects/my_site/.env
archaeoforge extract projects/my_site
```

The extractor can send eligible PDFs directly as multimodal file inputs, analyze native images, or analyze indexed text windows. It returns a structured claim schema rather than free-form notes.

Controls applied automatically:

- All generated claims receive `needs_review` status.
- A model-proposed Class A claim is downgraded to Class B pending human verification.
- Confidence is capped.
- Exact quotations cannot be fabricated by policy and are checked against indexed text when available.
- Each claim stores the model and response ID.

AI use is billed separately by the API provider. Source licensing and privacy remain the operator's responsibility.

## Review claims

List claims:

```bash
archaeoforge claims projects/my_site
```

Approve one after source review:

```bash
archaeoforge review EVID-001 approved \
  --project projects/my_site \
  --reviewer "Reviewer name" \
  --notes "Checked plan 14 and section B-B against the scanned report"
```

Every status change is appended to `claim_reviews`. It is not silently overwritten.

Export the working register:

```bash
archaeoforge export-evidence projects/my_site
```

## Author geometry

Geometry is authored in `data/features.geojson`. A feature links geometry to reviewed evidence:

```json
{
  "type": "Feature",
  "properties": {
    "id": "TEMPLE-01",
    "template": "temple",
    "review_status": "approved",
    "evidence_class": "B",
    "confidence": 0.82,
    "date_start": -330,
    "date_end": -280,
    "evidence_ids": ["EVID-TEMPLE-PLAN", "EVID-TEMPLE-HEIGHT"],
    "params": {
      "height": 11.5,
      "material": "mudbrick"
    }
  },
  "geometry": {
    "type": "Polygon",
    "coordinates": [[[0,0],[40,0],[40,28],[0,28],[0,0]]]
  }
}
```

The compiler rejects the feature when its own status, any linked claim status, chronology, source binding, or geometry fails policy.

## Georeference a historical plan

Create a GCP CSV with coordinates selected in QGIS or another controlled workflow:

```csv
enabled,pixel_x,pixel_y,map_x,map_y,note
true,105,92,448100,3595100,northwest foundation corner
true,1905,100,449000,3595100,northeast foundation corner
true,108,1390,448100,3594450,southwest foundation corner
```

Preview commands without changing files:

```bash
archaeoforge georef plan.png gcps.csv plan_georef.tif \
  --project projects/my_site \
  --dry-run
```

Execute:

```bash
archaeoforge georef plan.png gcps.csv plan_georef.tif \
  --project projects/my_site
```

Affine transformation requires at least three enabled control points. Second-order polynomial transformation requires at least six. More well-distributed points are normally preferable to the mathematical minimum.

## Validate and compile

Preview inspection:

```bash
archaeoforge validate projects/my_site --preview
archaeoforge compile projects/my_site --preview
```

Authoritative build:

```bash
archaeoforge validate projects/my_site
archaeoforge compile projects/my_site
```

The authoritative mode requires all included features and linked claims to satisfy the configured evidence policy. Run the complete pipeline with:

```bash
archaeoforge run projects/my_site
```

## Blender templates

The bundled Blender script supports these templates:

- `terrain`, `context`
- `water`, `river`, `canal`
- `building`, `palace`, `temple`, `platform`
- `wall`, `city_wall`
- `road`, `processional`
- `ziggurat`
- `pyramid`
- `sphinx`
- `gate`
- `residential_cluster`
- `tree`, `palm`

All coordinates and dimensions use metres. Procedural generation is deterministic. Each Blender object stores feature ID, evidence IDs, evidence class, confidence, review status, provenance JSON, and the scene input fingerprint.

The native `pyramid` template builds a true planar pyramid from a point feature. Its parameters
include `base_size`, `height`, and `rotation_degrees`; `lower_casing_fraction` can divide the form
between `lower_material` and `upper_material`. The bundled material library includes limestone and
granite, so a casing distinction such as Menkaure's granite lower courses can be represented in the
evidence render instead of being delegated to image generation.

The native `sphinx` template also starts from a point feature. `overall_length`, `body_width`,
`height`, and `rotation_degrees` define a continuous low lion body, chest, paired forepaws, human
head, muzzle, and headdress guide; at zero rotation its face and paws point toward local +X. It is a
semantic evidence-render proxy, not a claim that the smooth component volumes reconstruct carving
detail.

Every native template has a conservative recognizability class: `generic_envelope`,
`type_specific`, or `identity_specific`. Unknown templates remain generic. A successful render
writes schema-1 `outputs/exports/blender_result.json`, binding the manifest hash and fingerprint,
the exact `beauty.png` hash and metadata, and every feature's template and recognizability. Blender
objects carry the same template semantics for inspection.

Build without rendering:

```bash
archaeoforge build projects/my_site
```

Build and render:

```bash
archaeoforge render projects/my_site
```

Set `blender.render_mode: evidence` to render Class A, B, C, and D geometry with distinct diagnostic materials.

## Camera, sun, and sky

The camera frames itself. `blender.camera.auto_frame` solves the camera distance from the eight corners of the compiled scene bounds, so the reconstruction fills the frame at any site size or aspect ratio. Position it with a surveyor's angles rather than coordinates:

```yaml
blender:
  camera:
    auto_frame: true
    azimuth_degrees: 152.0      # compass bearing of the camera from the site, 0 = north
    elevation_degrees: 22.0     # degrees above the horizon
    margin: 1.05                # 1.0 fits the bounds exactly
    target_height_bias: 0.0     # raises the aim point, as a fraction of site height
    frame_includes_context: false
    lens_mm: 40.0
```

Terrain and context sheets normally run well past the reconstruction, so they are excluded from the framing solve unless `frame_includes_context` is true. Set `auto_frame: false` to fall back to the explicit `location` and `target`.

The sun uses the same angular convention, and the sky is a real node-based world that supplies the ambient fill. Without it every surface facing away from the sun renders as a flat silhouette:

```yaml
blender:
  exposure: -1.0
  sun:
    elevation_degrees: 38.0
    azimuth_degrees: 232.0
    energy: 2.6
    angle_degrees: 1.6          # angular diameter, controls shadow softness
    rotation_degrees: null      # set elevation_degrees to null to use a raw Blender Euler instead
  sky:
    procedural_sky: true
    strength: 0.7
    turbidity: 3.0
    ground_albedo: 0.35
    dust_density: 1.5
```

`view_transform`, `look`, `shadows`, `raytracing`, `shadow_ray_count`, and `shadow_step_count` are also exposed on the `blender` block. A `look` your Blender build does not offer is reported on the console rather than silently dropped. Engine names changed across Blender versions; `BLENDER_EEVEE` and `BLENDER_EEVEE_NEXT` both resolve to whichever the installed build provides.

## Optional AI finishing

Finishing has two separate lanes. Both treat the result as a non-authoritative derived
presentation layer and bind it to the base-render hash and compiled-manifest hash.

Finishing also has two explicit intents. `precise_object_edit` is the default restrained
material-and-lighting pass: the render remains the authoritative spatial constraint and the
strict geometry audit applies. `historical_scene` permits proxy form, material, occupation, and
surface detail to change, but requires a project-authored spatial contract that names the
relationships image generation must preserve. Presentation-anchor protection is independent of
evidence review status: a selected preview feature may remain `needs_review` while its placement,
ordering, topology, orientation, or scale relationship is protected against accidental visual
drift. The request binds both the contract file and snapshots of its referenced manifest features.
For historical scenes it also binds the successful Blender render receipt, proving that Image 1 is
the current `outputs/renders/beauty.png` produced from that exact manifest and template set.

Historical-scene output is deliberately interpretive, so the strict pixel/geometry-preservation
audit remains inapplicable. A separate protected-anchor audit checks every required contract
constraint before publication. A failed, missing, incomplete, or low-confidence assessment blocks
the write. When automatic assessment is unavailable, only a named
`--spatial-recommendation accept` covering the complete contract may substitute; a general
historical-plausibility review does not satisfy the spatial gate.

### Codex built-in image generation

Codex's built-in image generator is an interactive session capability, not an importable
Python backend. Prepare a portable request:

```bash
archaeoforge prepare-finish \
  projects/my_site/outputs/renders/beauty.png \
  --project projects/my_site \
  --mode historical_scene \
  --prompt projects/my_site/prompts/finish_historical_scene.txt
```

Historical mode requires a project-relative contract path in `project.yaml`:

```yaml
ai:
  finish_mode: historical_scene
  historical_scene_spatial_contract: data/historical_scene_spatial_contract.json
```

The referenced JSON uses schema 1. Each required constraint names manifest feature IDs and a
visible relationship. Only feature IDs listed in `mutable_feature_ids` may be deliberately
relocated; a required protected feature cannot also be mutable. Optional
`base_render_requirements` make identity-critical landmarks fail closed when their native evidence
geometry is still only an unknown or generic envelope:

```json
{
  "spatial_contract_schema": 1,
  "constraints": [
    {
      "id": "MAIN-MONUMENT-STAGGER",
      "kind": "visible_stagger",
      "required": true,
      "feature_ids": ["MONUMENT-A", "MONUMENT-B", "MONUMENT-C"],
      "requirement": "Keep three distinct offsets in the selected northeast-to-southwest order.",
      "evidence_ids": ["EV-SPATIAL-01"]
    }
  ],
  "base_render_requirements": [
    {
      "id": "MONUMENT-SEMANTIC-BASE",
      "feature_ids": ["MONUMENT-A", "MONUMENT-B", "MONUMENT-C"],
      "minimum_recognizability": "type_specific",
      "requirement": "Image 1 must show native monument-type geometry, not generic boxes."
    }
  ],
  "mutable_feature_ids": [],
  "notes": "Proxy form and materials may change; the named relationship may not."
}
```

Historical request schema 4 reserves Image 1 for the fresh receipt-bound `beauty.png`. A prior
candidate or registered finish may inform a later refinement only as a supporting appearance
reference: bind it, a plan, or a comparison image explicitly by repeating `--reference-image PATH`.
Supporting references are allowed only in `historical_scene`; each gets an image index from 2
onward, role, project-relative path, dimensions, format, and SHA-256 in the request and provenance.
They never replace or override Image 1, its receipt, or the spatial contract.

Open the emitted `outputs/exports/image_finish_request.json` with the project in Codex and
ask Codex to follow `suggested_codex_prompt`. Keep the tool result at its separate candidate
path; only `register-finish` may publish the requested final path:

```bash
archaeoforge register-finish path/to/candidate.png \
  --project projects/my_site \
  --request projects/my_site/outputs/exports/image_finish_request.json
```

Registration detects stale bindings or request edits whose checksums were not deliberately
recomputed, and rejects a non-beauty historical base, missing or stale render receipt, changed
beauty image, changed supporting reference, changed manifest or feature-template semantics, changed
spatial contract or protected-feature snapshot, invalid image, changed frame, unsafe output path,
accidental base-image overwrite, or an existing image/provenance/audit set. Historical
spatial validation also runs before the image is copied to the final path. A blocked
`register-finish` reports `validation_blocked` and exits with status 2. Requests are confined to
`outputs/exports`; published PNGs are confined to
`outputs/renders`. If an interactive provider returns a different resolution at the same
aspect ratio, the explicit `--normalize-size` option resamples it and permanently leaves the
result review-required. Use `--manual-recommendation accept|review|reject --reviewer ...
--review-notes ...` to record a visual review. Nothing is silently approved. In
`historical_scene` mode this is a historical-plausibility/presentation review, not spatial-contract
acceptance or a claim that the generated city plan is archaeological evidence. Provenance records
its scope as `historical_plausibility`; precise edits use `geometry_preservation`.

To have `archaeoforge run` prepare the request after a successful render:

```yaml
ai:
  finish_enabled: true
  finish_backend: interactive_handoff
  finish_mode: historical_scene      # or precise_object_edit
  historical_scene_spatial_contract: data/historical_scene_spatial_contract.json
  image_input_fidelity: high       # compatibility key; GPT Image 2 applies this automatically
  image_size: auto
```

The run reports `pending_external_finish`; it does not claim the interactive generation has
already happened.

### Public OpenAI Image API

For an unattended API-backed edit, set `OPENAI_API_KEY` locally and run:

```bash
archaeoforge finish \
  projects/my_site/outputs/renders/beauty.png \
  --project projects/my_site
```

The API lane uses the configured `ai.image_model`. Per the
[official GPT Image guide](https://developers.openai.com/api/docs/guides/image-generation#image-input-fidelity),
GPT Image 2 automatically processes image inputs at high fidelity, so ArchaeoForge omits the
unsupported request parameter and records the effective value as `automatic_high`; the compatibility
config key accepts only `high`. The output size is derived from the base render when `image_size: auto`,
and GPT Image 2 dimensions—including its 655,360-pixel minimum—are validated before a paid call.
Only `OPENAI_API_KEY` is read from the selected project's `.env`,
and the client is pinned to the official OpenAI API endpoint. `--audit` or `--no-audit` overrides
`geometry_audit_enabled`; with neither flag, the project setting is respected in
`precise_object_edit` mode. The optional multimodal audit returns an accept, review, or reject
recommendation. It is a quality-control aid, not proof that geometry was preserved, and an audit
failure leaves an explicit review-required provenance record rather than an orphaned image. The
strict geometry audit is bound off for `historical_scene`; explicitly requesting `--audit` in that
mode is an error. Historical output stays non-authoritative even when a named, unnormalized
historical-plausibility acceptance clears its review-required flag.

Finish publication treats the PNG, provenance JSON, and audit JSON as one output set. Defaults
version around any member of an existing set; explicit replacement requires `--force`, and stale
audit sidecars are removed. Run one finish publisher per project at a time.

## Reproducibility and controls

The scene manifest includes:

- Input fingerprint
- Build mode
- Exact project date
- Compiled and excluded features
- Effective evidence class and confidence
- Claim-level source provenance
- Registered and current source checksums
- Blender configuration

The fingerprint is stable when the relevant inputs do not change. Timestamps are not part of the fingerprint.

## Tests

```bash
source .venv/bin/activate
pytest
```

The test suite covers evidence gates, chronology, checksum mutation, review auditing,
ingestion, GCP validation, source-catalog merging, AI claim downgrading, historical year
validation, camera framing, strict configuration, safe initialization, report escaping and
path redaction, object indices, mitred line geometry, image-finish handoffs and provenance,
and the Babylon starter.

`tests/test_blender_render.py` drives a real Blender and checks the things a host-side test cannot see: that the site actually fills the frame, that the sky lights the scene, that the render passes land under the documented names, and that an unavailable colour-management look is reported. It is skipped when no Blender is found. Point it at a specific build with `ARCHAEOFORGE_BLENDER=/path/to/blender pytest`.

## Recommended production roles

A serious reconstruction should designate at least:

- Research lead
- Source librarian or provenance owner
- GIS and survey lead
- Architectural reconstruction lead
- Material-culture specialists
- 3D technical artist
- Independent reviewer

One person can hold multiple roles in a small project, but approval responsibility should remain explicit.

## License

ArchaeoForge's own source is MIT licensed. Source documents, scans, museum images, textures,
dependencies, services, and third-party models retain their own licenses. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), including the separate PyMuPDF licensing terms.
