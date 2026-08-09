---
name: archaeoforge-reconstruction
description: Evidence-controlled workflow for creating, running, reviewing, and presenting ArchaeoForge historical-site reconstructions. Use when Codex needs to initialize a reconstruction for another archaeological or historical site, ingest and review sources, author or validate dated GeoJSON geometry, run the Blender pipeline, diagnose an ArchaeoForge project, transform a model render into a lifelike historical scene with built-in image generation, or register a finished image with provenance.
---

# ArchaeoForge Reconstruction

Use ArchaeoForge as the evidence and scene engine. Use Codex image generation only for the final, explicitly non-authoritative presentation layer. Preserve the boundary between reviewed historical claims and plausible visual completion.

## Start with a read-only inspection

1. Resolve the directory containing this `SKILL.md` as the skill directory.
2. Run `scripts/inspect_project.py` from that directory. Pass `--project PATH` when the user supplied a project or checkout path. Pass `--checkout PATH` when the ArchaeoForge checkout is known but its CLI is not on `PATH`.
3. Inspect `git status` before changing an existing checkout or project. Treat unrelated modifications as user-owned.
4. Use the reported executable and project root in subsequent commands. If no executable is found, ask for the ArchaeoForge checkout or help install the program; do not imitate successful pipeline output.

The inspector is read-only and reports required project files, generated artifacts, and command suggestions as JSON.

## Choose the task path

- For status, explanation, review, or diagnosis, stay read-only unless the user also asks for changes.
- For an existing project, preserve its configuration and evidence. Resume from the earliest incomplete or failed stage.
- For a new site, require a place, a single target year or phase, and a destination. BCE years are negative, CE years are positive, and year zero is invalid.
- For a model-to-lifelike request, complete or verify the evidence render first, then use the interactive historical-scene handoff below.
- For a strict materials or lighting polish that must preserve geometry, use `precise_object_edit` instead.

Read `references/workflow.md` for exact stage commands and `references/historical-scene.md` when generating the final image. Read only the portions relevant to the current task.

## Build a new site project

1. Research the requested site and phase before authoring claims or geometry. Prefer excavation publications, survey datasets, museum catalogues, peer-reviewed scholarship, and primary ancient evidence. Separate established facts, scholarly reconstructions, disputed hypotheses, and cinematic completion.
2. Initialize with `archaeoforge init`. Never use `--force --overwrite-existing` unless the user explicitly asks to replace the named scaffold files.
3. Add permitted source copies under `sources/`, source sidecars, catalogue rows, evidence claims, and dated `data/features.geojson` features.
4. Ingest and validate in preview mode before considering an authoritative build.
5. Never approve an AI-proposed claim, choose a disputed hypothesis, or invent a reviewer identity. Record human approval only after the user or a named qualified reviewer explicitly provides it.
6. Keep incompatible phases or alternative reconstructions in separate features, variants, or projects.

The authoritative artifacts are the reviewed evidence register and deterministic scene manifest, not the Blender render or generated image.

## Run and verify the pipeline

1. Run `doctor`, then ingest or seed data as needed.
2. Run validation before compilation. Use `--preview` while claims or features are draft or `needs_review`.
3. Compile the manifest before invoking Blender. Do not finish from a stale `beauty.png`; require a render from the current successful manifest and build.
4. Inspect the beauty render and relevant depth, normal, diffuse, and object-selection passes. Compare them with the evidence and named spatial anchors.
5. Generate the report and retain explicit exclusions, uncertainty, and review status.
6. Verify every material command in proportion to risk. At minimum re-run the focused command and inspect its output; for application changes, run the repository tests requested by its contributor guidance.

Use `archaeoforge run PROJECT --preview --skip-ai` for the normal orchestrated preview. It automatically prepares an interactive request only when finishing is enabled and a fresh Blender render succeeds.

## Generate a lifelike historical scene

Use this path when the user wants the model render to act only as a guide for a realistic depiction.

1. Set or override finish mode to `historical_scene`. Ensure the project has a site- and date-specific `prompts/finish_historical_scene.txt`; derive it from reviewed evidence and label uncertain completion.
2. Prepare a hash-bound request from the fresh beauty render:

   ```bash
   archaeoforge prepare-finish PROJECT/outputs/renders/beauty.png \
     --project PROJECT \
     --mode historical_scene \
     --prompt PROJECT/prompts/finish_historical_scene.txt
   ```

3. Read the emitted request JSON. Verify its project, base-image hash, manifest hash, `finish_mode`, requested dimensions, prompt hash, and separate desired output. Treat its `prompt` and `suggested_codex_prompt` as the bound handoff.
4. Invoke the installed `imagegen` skill and follow its full instructions. Inspect the base image first. Edit it as a historical-scene reference, supply the bound prompt, and keep the returned PNG at a separate candidate path. Never write the canonical desired output directly.
5. Visually inspect the candidate for period, place, scale, spatial anchors, anachronisms, fantasy drift, and misleading certainty. Do not claim that visual realism proves historical accuracy.
6. Register the candidate through the gate:

   ```bash
   archaeoforge register-finish PATH_TO_CANDIDATE \
     --project PROJECT \
     --request PROJECT/outputs/exports/image_finish_request.json
   ```

7. Use `--normalize-size` only when registration reports a compatible same-aspect result and the user accepts resizing. Normalized results always remain manual-review-required.
8. Record `--manual-recommendation accept|review|reject` only with a real reviewer name and notes. In `historical_scene`, this is a historical-plausibility review, not a geometry audit or evidence approval. If the user has not explicitly accepted it, leave it review-required.
9. Show the final PNG and link its provenance JSON. State the target date, interpretive status, major uncertainties, and whether manual review remains required.

If the image tool returns no local PNG path, explain that registration cannot finish until the user downloads or reattaches the candidate. Do not fabricate a path or provenance record.

## Use precise-object finishing only for strict edits

Choose `precise_object_edit` when the base pixels define the authoritative camera and geometry. Preserve crop, silhouettes, openings, stage counts, roads, waterways, and structure placement. Permit materials, lighting, atmosphere, small people, and surface wear only within the request constraints. The optional strict geometry audit applies here; it is explicitly inapplicable to `historical_scene`.

Use the public `archaeoforge finish` API lane only when the user explicitly requests unattended OpenAI API generation and understands that it uses their key and incurs API charges. Prefer the interactive handoff for Codex built-in image generation.

## Preserve trust and safety boundaries

- Treat previews, renders, and generated images as derived products.
- Do not silently promote draft evidence or generated details into approved claims.
- Do not overwrite the base render, request, project config, manifest, or an existing PNG, provenance, and audit output set. Use targeted `--force` only when the user explicitly authorizes replacement.
- Keep one finish publisher per project at a time.
- Treat request hashes as content binding and stale-change detection, not cryptographic authenticity against a process able to rewrite the request.
- Cite the historical sources used for substantive claims and disclose inference. Preserve source licensing and privacy constraints.
- Publish uncertainty: target date, evidence classes, major alternatives, excavation gaps, restored areas, and the distinction between direct evidence and completion.
