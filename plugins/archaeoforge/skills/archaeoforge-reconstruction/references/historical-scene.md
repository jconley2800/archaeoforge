# Historical-scene image guidance

## Intent

Treat the Blender beauty render as an evidence-controlled spatial reference for an interpretive presentation. Replace proxy forms, a display slab, empty context, and model-like materials with a coherent life-size inhabited setting while preserving every relationship named in the project's spatial contract. Evidence review status and presentation-anchor protection are separate: `draft` or `needs_review` records communicate archaeological uncertainty, but do not give the image generator permission to relocate a selected feature. Do not describe the result as measured geometry or archaeological evidence.

Use `precise_object_edit` instead when the user wants an exact pixel- and geometry-preserving finish.

## Build the prompt from evidence

Write `prompts/finish_historical_scene.txt` with these sections in this order:

1. **Identity and moment** — place, target year or phase, and season or time of day only if supported or explicitly illustrative.
2. **Reference role** — state that the base is a spatial and compositional guide, not the target style.
3. **Protected relationships and selected corrections** — name the camera, viewpoint, waterways, streets, monuments, districts, walls, terrain, horizons, and relative relationships that must remain legible. State any deliberate source-led relocation explicitly and list that feature as mutable in the contract rather than relying on prose alone.
4. **Ordinary built fabric** — locally appropriate construction, density, roofs, lanes, workshops, wear, and scale.
5. **Monuments** — evidence-led materials, approximate dimensions, and restrained treatment of uncertain upper portions.
6. **Environment** — terrain, hydrology, cultivated land, vegetation, weather, dust, haze, and physically plausible light.
7. **Daily life** — period-appropriate people, dress, animals, transport, craft, commerce, and water activity at believable scale.
8. **Uncertainty rules** — keep disputed or weakly supported details sober; omit named features that the selected hypothesis rejects.
9. **Negative constraints** — anachronistic architecture, modern objects, fantasy motifs, excessive grandeur, ruins when depicting a functioning phase, text, labels, logos, borders, and watermarks.
10. **Output** — photorealistic documentary character, exact requested aspect and dimensions, and no diorama, model, or game-map appearance.

Separate direct evidence from visual completion. Class D details may make the scene lived-in, but they must not change the site plan or masquerade as known facts.

## Author the spatial contract

Set `ai.historical_scene_spatial_contract` in `project.yaml` to a project-relative JSON path. The
file uses `spatial_contract_schema: 1` and contains at least one constraint:

```json
{
  "spatial_contract_schema": 1,
  "constraints": [
    {
      "id": "MAIN-MONUMENT-STAGGER",
      "kind": "visible_stagger",
      "required": true,
      "feature_ids": ["MONUMENT-A", "MONUMENT-B", "MONUMENT-C"],
      "requirement": "Keep all three monuments visibly staggered in the selected order; do not collapse them into a row.",
      "evidence_ids": ["EV-SPATIAL-01"]
    }
  ],
  "mutable_feature_ids": [],
  "notes": "Proxy form and materials may change; the protected relationship may not."
}
```

Allowed constraint kinds are `relative_layout`, `visible_stagger`, `presence`, `topology`,
`orientation`, and `scale_hierarchy`. Use stable constraint IDs and exact manifest feature IDs.
`evidence_ids` may bind the relationship to evidence already present in the compiled manifest. A
required protected feature cannot also appear in `mutable_feature_ids`; use that list only for a
deliberate selected-reconstruction relocation.

Preparation writes request schema 4. It requires the current receipt-bound
`outputs/renders/beauty.png` as Image 1, hash-binds the contract and completed schema-1 Blender
render receipt, copies the constraints and semantic base-render requirements, and snapshots each
protected feature's manifest geometry, parameters, template, recognizability, review status, and
evidence IDs. It then places a `NON-NEGOTIABLE SPATIAL CONTRACT` block before the site prompt.
Supporting references can inform appearance or an explicitly selected correction only from Image 2
onward; they cannot replace the fresh beauty render or override a protected constraint. If the
contract, receipt, beauty bytes, template semantics, or protected manifest snapshots change,
recompile, rerender, and prepare a new request.

## Image-generation procedure

1. Read the hash-bound request and use its complete `prompt`, not a remembered or generic site prompt.
2. Inspect the base image before editing. Locate every protected feature and trace every required relationship named in `spatial_contract`.
3. Invoke the installed `imagegen` skill with the base image as Image 1 and any hash-bound `reference_images` in their recorded image-index order. Use the request prompt as the governing instructions. Never pass an unbound prior candidate, plan, diagram, or comparison image.
4. Ask for a lifelike historical scene, not a precise object edit. Proxy form, surface materials, occupation, vegetation, and atmosphere may change, but the generator must not regularize, align, merge, obscure, omit, or relocate protected relationships.
5. Keep the returned file as a candidate. Do not target the request's `desired_output.path`.
6. Inspect the candidate at full frame before registration and check every required constraint by ID.
7. Register the candidate. The historical protected-anchor audit must complete before publication; a failure or incomplete constraint set blocks the output and makes the CLI exit 2.

If the tool cannot return the exact requested dimensions but preserves aspect ratio, register only with an explicit `--normalize-size` decision. Record that transform and keep manual review required.

## Visual review checklist

Check all of the following:

- The site, phase, landscape, and climate read correctly.
- Every required contract ID is visibly satisfied; protected features remain present and distinct.
- Viewpoint and crop preserve the legibility of the protected relationships.
- People, doors, streets, animals, boats, carts, and buildings share believable scale.
- Ordinary urban or rural fabric is plausible rather than dominated by monuments.
- Materials and construction match the period and region.
- Uncertain heights, roofs, decoration, vegetation, and occupation are restrained.
- No fantasy, modern, culturally misplaced, or chronologically late elements appear.
- The scene does not look like a miniature, studio maquette, game map, painting, or pristine CGI asset.
- The image has no labels, watermarks, borders, or invented explanatory text.
- Atmospheric polish has not hidden obvious contradictions in the evidence render.

For `historical_scene`, do not run or claim a strict pixel/geometry-preservation audit; that mode intentionally permits substantial replacement of proxy geometry. Registration instead uses the narrower historical protected-anchor assessment. Its structured result must cover every required constraint exactly once, preserve viewpoint/crop, retain protected features, meet the confidence threshold, and recommend acceptance.

If automatic spatial assessment is unavailable, a real named reviewer may use
`--spatial-recommendation accept --reviewer ... --review-notes ...` only after inspecting the
complete bound contract. It cannot override a substantive automatic failure. Keep this review
separate from `--manual-recommendation`, which records historical plausibility and presentation
quality. Neither kind of acceptance approves evidence claims, hypotheses, or geometry.

## Final handoff

Show the registered PNG and provenance sidecar. State:

- the depicted place and target date
- that the image is interpretive and non-authoritative
- the base render and manifest it is bound to
- the spatial-contract path and protected-anchor audit status
- major uncertain or comparative elements
- whether it was normalized
- the manual recommendation and reviewer, or that review is still required
