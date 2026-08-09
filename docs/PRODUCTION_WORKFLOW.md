# Production workflow

## 1. Define the historical state

Record the target year, date tolerance, phase boundaries, season, construction state, geographic extent, and intended publication standard. Do not use one scene to represent several incompatible phases.

## 2. Build the source corpus

Acquire permitted copies of excavation reports, plans, sections, survey exports, scans, museum records, object catalogs, ancient texts, photographs, and relevant comparative publications. Create source sidecars and ingest the corpus.

## 3. Georeference plans

Use surveyed remains, fixed excavation grid points, or other defensible anchors. Preserve original scans. Keep separate transformations for plans that represent different phases or survey systems. Document residual error and rejected GCPs outside this starter pipeline.

## 4. Create claims

Use AI extraction only as a first-pass indexing assistant. Review page images and exact figures. Split claims by property and hypothesis. Record uncertainty and alternative groups. Avoid claims that merely restate a modern illustration.

## 5. Approve evidence

A reviewer should verify:

- Source identity and version
- Locator
- Quotation or visual basis
- Measurement and unit
- Date applicability
- Evidence class
- Confidence
- Alternative interpretation

Use the CLI review command to record approval and notes.

## 6. Author spatial features

Digitize surveyed or reconstructed plans in QGIS and export GeoJSON in the project's metric coordinate system. Link each feature to the smallest set of claims that supports its geometry and parameters.

## 7. Validate hypotheses separately

Use separate features or project branches for mutually exclusive reconstructions. Do not combine alternative height, circulation, phase, or placement hypotheses in one feature.

## 8. Compile and inspect the evidence render

Set `blender.render_mode: evidence`. Look for weak Class C or D material in prominent
positions. Review exclusions and object metadata. Compare orthographic views against the
original plans. Use `outputs/exports/object_index_map.json` to decode dense object indices;
on Blender 5.2 inspect `cryptomatte_object.exr`, because that build does not expose the
compositor `IndexOB` socket.

## 9. Render the realistic scene

Return to realistic materials only after the evidence render passes review. Inspect depth,
normal, diffuse, and the object-selection pass available in your Blender build. Keep neutral
lighting renders for audit.

## 10. Finish cautiously

AI finishing should be local and reversible. Run `prepare-finish` before using Codex's
built-in image editor, keep the returned image at a separate candidate path, then run
`register-finish` to publish it. Never let the editor write directly to the canonical final
path. The request and result record bind the image to the exact base render, prompt, and scene
manifest. Use the public API `finish` command only when an unattended API call is intended.

Choose the finish intent explicitly. In `precise_object_edit` mode, reject outputs that alter
camera, crop, silhouettes, openings, stage counts, wall lines, roads, waterways, or monument
placement. In `historical_scene` mode, the evidence render is only a broad layout and viewpoint
guide: the generator may replace schematic blocks with a lifelike inhabited setting, but the
result is an interpretive illustration and begins manual-review-required. The strict geometry
audit is not applicable to that mode. Only a named `historical_plausibility` acceptance may clear
an unnormalized historical result; a resized result or a `review` or `reject` recommendation remains
review-required. Visual polish and review never promote the image to evidence.

## 11. Publish uncertainty

A public reconstruction should state the target date, evidence classes, major alternatives, missing excavation coverage, modern restoration boundaries, and the difference between direct evidence and completion.
