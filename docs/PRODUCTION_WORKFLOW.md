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
lighting renders for audit. Inspect `outputs/exports/blender_result.json` as well: a completed
schema-1 render receipt must bind the current manifest hash and fingerprint, the exact beauty-image
hash and metadata, and the per-feature template/recognizability table. Recompile and rerender after
any feature, template, manifest, camera, or beauty-image change.

## 10. Finish cautiously

AI finishing should be local and reversible. Run `prepare-finish` before using Codex's
built-in image editor, keep the returned image at a separate candidate path, then run
`register-finish` to publish it. Never let the editor write directly to the canonical final
path. The request and result record bind the image to the exact base render, prompt, and scene
manifest. Historical mode always uses the receipt-bound current
`outputs/renders/beauty.png` as Image 1. For later iterations, repeat `--reference-image PATH` to
bind any prior candidate, registered finish, plan, or diagram only as an appearance reference from
Image 2 onward; an unbound or prior generated image must never replace the spatial base. Use the
public API `finish` command only when an unattended API call is intended.

Choose the finish intent explicitly. In `precise_object_edit` mode, reject outputs that alter
camera, crop, silhouettes, openings, stage counts, wall lines, roads, waterways, or monument
placement. In `historical_scene` mode, configure a project-relative
`ai.historical_scene_spatial_contract` before preparing the request. Its JSON constraints name the
manifest feature IDs and visible presence, layout, stagger, topology, orientation, or scale
relationships that must survive interpretation. List a feature in `mutable_feature_ids` only when
deliberate relocation is part of the selected reconstruction; a required protected feature cannot
also be mutable. Archaeological review status remains independent: a preview feature can be
`needs_review` and still be protected from presentation drift.

For an identity-critical landmark, add a `base_render_requirements` entry with that protected
feature ID, a stable requirement ID, explanatory text, and `minimum_recognizability` of
`type_specific` or `identity_specific`. ArchaeoForge classifies each native feature conservatively as
`generic_envelope`, `type_specific`, or `identity_specific`; unknown templates are generic. If a
required landmark is still a generic box, platform, or unknown template, historical finishing stops
before a request is written. Use a native semantic template—such as `pyramid` or the continuous,
east-facing `sphinx` proxy—then recompile and rerender. This readiness check protects visual identity
without promoting smooth proxy anatomy or surface detail into evidence.

Request schema 4 hash-binds the contract, its semantic base-render requirements, protected feature
geometry and parameters, and the successful render receipt. Confirm that the emitted prompt begins
with the non-negotiable spatial contract and records the satisfied semantic requirements before
invoking image generation. The generator may replace proxy meshes, materials, vegetation,
occupation, and surface detail with a lifelike setting, but it may not regularize, align, merge,
hide, omit, detach, duplicate, or relocate a protected relationship or identity-critical form.
Supporting images remain secondary references and cannot override the fresh beauty render,
receipt, or contract.

Registration revalidates the request, contract, manifest snapshots, receipt, beauty-image bytes,
feature-template semantics, supporting references, and candidate, then runs the historical
protected-anchor assessment before publishing. A missing receipt, stale manifest receipt, changed
beauty image, generated candidate supplied as Image 1, or unmet recognizability threshold is a
fail-closed error. The assessment must cover every required constraint exactly once and pass the
viewpoint/crop, protected-feature, confidence, and acceptance checks. Any missing or failed check
blocks publication; `register-finish` reports
`validation_blocked` and exits 2 without writing the final PNG or provenance sidecar. If automatic
assessment is unavailable, a real named reviewer who checked the complete contract may explicitly
supply `--spatial-recommendation accept --reviewer NAME --review-notes NOTES`. Do not use that
fallback when the automatic audit returned a substantive contract failure, and do not treat
`--manual-recommendation accept` as spatial acceptance.

The strict pixel/geometry-preservation audit remains inapplicable to `historical_scene`; the
protected-anchor assessment is a separate, narrower gate designed to allow historically motivated
proxy replacement without allowing site-plan drift. The registered result is still an interpretive
illustration and begins manual-review-required. A named `historical_plausibility` acceptance may
clear that presentation-review flag for an unnormalized result; normalization or a general `review`
or `reject` recommendation keeps it review-required. Visual polish and review never promote the
image to evidence.

## 11. Publish uncertainty

A public reconstruction should state the target date, evidence classes, major alternatives, missing excavation coverage, modern restoration boundaries, and the difference between direct evidence and completion.
