# ArchaeoForge cross-site workflow

## Resolve the engine and project

Run the bundled read-only inspector before choosing commands:

```bash
python3 SKILL_DIR/scripts/inspect_project.py --project PROJECT_OR_CHECKOUT
```

Prefer `archaeoforge` on `PATH`. Otherwise use a discovered checkout-local `.venv/bin/archaeoforge` or pass `--checkout CHECKOUT` to the inspector. Do not assume a checkout is installed merely because a project directory exists.

## Initialize a site

```bash
archaeoforge init projects/site_slug \
  --title "Site, target phase" \
  --place "Site" \
  --year -300 \
  --label "approximately 300 BCE"
```

Use negative integers for BCE and positive integers for CE. There is no year zero. `init` refuses to replace scaffold files. `--force` may add missing files to a non-empty directory; `--force --overwrite-existing` replaces managed scaffold files and therefore requires explicit user authorization.

## Populate the evidence layer

The standard project layout is:

```text
project.yaml
sources/
data/source_catalog.csv
data/evidence_seed.csv
data/features.geojson
prompts/finish.txt
prompts/finish_historical_scene.txt
.archaeoforge/
outputs/
```

For each local source, place the permitted file under `sources/` and add a sibling sidecar such as `report.pdf.source.yaml`:

```yaml
id: SRC-REPORT-001
title: Excavation report title
authors: Author or institution
publication_year: 1925
source_type: excavation_report
license: Public domain
notes: Edition and page-image details.
```

Run ingestion, then inspect claims:

```bash
archaeoforge ingest PROJECT --render-visual-pages
archaeoforge claims PROJECT
```

Optional API extraction is a proposal stage only:

```bash
archaeoforge extract PROJECT
```

It requires explicit user intent, local source permission, `ai.enabled: true`, and an OpenAI API key. Generated claims remain `needs_review`; never invent approval.

Record a real review:

```bash
archaeoforge review EVIDENCE_ID approved \
  --project PROJECT \
  --reviewer "Reviewer name" \
  --notes "Exact source and locator checked"
```

## Author geometry

Store dated metric features in `data/features.geojson`. Give every feature a stable ID, template, review status, evidence class, confidence, applicable date interval, evidence IDs, and parameters. Keep mutually exclusive hypotheses separate. Use georeferenced plans and explicit reviewed ground-control points where available; do not derive measurements from a persuasive illustration.

## Validate, compile, render, and report

Use the orchestrator for the normal preview path:

```bash
archaeoforge doctor PROJECT
archaeoforge run PROJECT --preview --skip-ai
```

Use `--skip-blender` only when inspecting the evidence, manifest, and report stages. Do not perform image finishing after a skipped or failed Blender stage, even if an older `outputs/renders/beauty.png` exists.

For manual stage isolation, consult `archaeoforge COMMAND --help`, then run the equivalent sequence:

```text
seed or ingest -> validate -> compile -> build -> render -> report
```

Validation errors must stop an authoritative build. Preview output may contain draft material and must remain labelled as preview.

After rendering, inspect:

- `outputs/exports/scene_manifest.json`
- `outputs/exports/evidence_register.csv`
- `outputs/exports/object_index_map.json`
- `outputs/reports/validation.json`
- `outputs/reports/index.html`
- `outputs/renders/beauty.png`
- available depth, diffuse, normal, and object-selection passes

## Prepare an interactive image handoff

For a lifelike interpretive reconstruction:

```bash
archaeoforge prepare-finish PROJECT/outputs/renders/beauty.png \
  --project PROJECT \
  --mode historical_scene \
  --prompt PROJECT/prompts/finish_historical_scene.txt
```

For a geometry-preserving material pass, select `--mode precise_object_edit` and normally use `prompts/finish.txt`.

The request under `outputs/exports/` binds:

- project identity and target date
- base image path, SHA-256, dimensions, and format
- compiled manifest path, SHA-256, and input fingerprint
- finish mode, prompt, and prompt SHA-256
- requested output path and dimensions
- provider and model hints and audit policy

Use the request's actual filename when multiple requests exist. Read its `suggested_codex_prompt`; do not assume the unnumbered default is current.

## Register the candidate

Never have an editor write the requested final destination directly. Keep a candidate at a separate path, then publish through:

```bash
archaeoforge register-finish CANDIDATE.png \
  --project PROJECT \
  --request PROJECT/outputs/exports/image_finish_request.json
```

Registration rejects stale base or manifest bindings, invalid or wrong-format images, incompatible frames, reserved targets, and output-set collisions. It writes the canonical PNG and provenance JSON together. A historical-scene request binds strict geometry audit off. A manual recommendation requires a reviewer:

```bash
archaeoforge register-finish CANDIDATE.png \
  --project PROJECT \
  --request REQUEST.json \
  --manual-recommendation review \
  --reviewer "Reviewer name" \
  --review-notes "Historical plausibility review"
```

Use `--normalize-size` only for a compatible same-aspect candidate. Normalization is recorded and keeps manual review required. Do not use `--force` unless replacing the exact known output set is explicitly authorized.
