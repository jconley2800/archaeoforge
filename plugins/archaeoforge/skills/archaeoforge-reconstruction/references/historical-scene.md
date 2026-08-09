# Historical-scene image guidance

## Intent

Treat the Blender beauty render as a broad spatial and compositional guide. Replace schematic blocks, a display slab, empty context, and model-like materials with a coherent life-size inhabited setting. Preserve the viewpoint and named site relationships that the evidence supports. Do not describe the result as measured geometry or archaeological evidence.

Use `precise_object_edit` instead when the user wants an exact pixel- and geometry-preserving finish.

## Build the prompt from evidence

Write `prompts/finish_historical_scene.txt` with these sections in this order:

1. **Identity and moment** — place, target year or phase, and season or time of day only if supported or explicitly illustrative.
2. **Reference role** — state that the base is a spatial and compositional guide, not the target style.
3. **Fixed broad anchors** — camera and viewpoint plus a short list of named waterways, streets, monuments, districts, walls, terrain, or horizons supported by the manifest.
4. **Ordinary built fabric** — locally appropriate construction, density, roofs, lanes, workshops, wear, and scale.
5. **Monuments** — evidence-led materials, approximate dimensions, and restrained treatment of uncertain upper portions.
6. **Environment** — terrain, hydrology, cultivated land, vegetation, weather, dust, haze, and physically plausible light.
7. **Daily life** — period-appropriate people, dress, animals, transport, craft, commerce, and water activity at believable scale.
8. **Uncertainty rules** — keep disputed or weakly supported details sober; omit named features that the selected hypothesis rejects.
9. **Negative constraints** — anachronistic architecture, modern objects, fantasy motifs, excessive grandeur, ruins when depicting a functioning phase, text, labels, logos, borders, and watermarks.
10. **Output** — photorealistic documentary character, exact requested aspect and dimensions, and no diorama, model, or game-map appearance.

Separate direct evidence from visual completion. Class D details may make the scene lived-in, but they must not change the site plan or masquerade as known facts.

## Image-generation procedure

1. Read the hash-bound request and use its complete `prompt`, not a remembered or generic site prompt.
2. Inspect the base image before editing.
3. Invoke the installed `imagegen` skill with the base image as the referenced image and the request prompt as the governing instructions.
4. Ask for a lifelike historical scene, not a precise object edit. Tell the generator which broad relationships and viewpoint to retain and which schematic or model cues to remove.
5. Keep the returned file as a candidate. Do not target the request's `desired_output.path`.
6. Inspect the candidate at full frame before registration.

If the tool cannot return the exact requested dimensions but preserves aspect ratio, register only with an explicit `--normalize-size` decision. Record that transform and keep manual review required.

## Visual review checklist

Check all of the following:

- The site, phase, landscape, and climate read correctly.
- Named broad anchors remain in the intended relationships.
- People, doors, streets, animals, boats, carts, and buildings share believable scale.
- Ordinary urban or rural fabric is plausible rather than dominated by monuments.
- Materials and construction match the period and region.
- Uncertain heights, roofs, decoration, vegetation, and occupation are restrained.
- No fantasy, modern, culturally misplaced, or chronologically late elements appear.
- The scene does not look like a miniature, studio maquette, game map, painting, or pristine CGI asset.
- The image has no labels, watermarks, borders, or invented explanatory text.
- Atmospheric polish has not hidden obvious contradictions in the evidence render.

For `historical_scene`, do not run or claim a strict geometry-preservation audit; that mode intentionally permits substantial replacement of schematic geometry. Use a named historical-plausibility review. An `accept` recommendation approves the derived illustration for presentation only; it does not approve claims, hypotheses, or geometry.

## Final handoff

Show the registered PNG and provenance sidecar. State:

- the depicted place and target date
- that the image is interpretive and non-authoritative
- the base render and manifest it is bound to
- major uncertain or comparative elements
- whether it was normalized
- the manual recommendation and reviewer, or that review is still required
