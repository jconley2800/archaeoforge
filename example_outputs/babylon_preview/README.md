# Generated Babylon preview output

These files were generated from the bundled schematic Babylon starter in preview mode without AI extraction or GDAL execution. The manifest, register, validation, and report are deterministic pipeline examples. `beauty.png` is a Blender model render, included only as a portable image-finish reviewer fixture.

- `scene_manifest.json` is the deterministic Blender-neutral scene input.
- `evidence_register.csv` is the claim register.
- `validation.json` records zero errors and four warnings for external-only bibliographic sources.
- `report.html` is a standalone review report.
- `beauty.png` is the matching 1536x864 schematic base render with local metadata removed.
- `candidate-512x288.png` is a same-aspect, metadata-free PNG for testing explicit size normalization. It is not an approved final reconstruction.

To materialize a disposable fixture without running Blender:

```bash
REVIEW_ROOT="$(mktemp -d)"
SITE_COPY="$REVIEW_ROOT/babylon_570_bce"
cp -a projects/babylon_570_bce "$SITE_COPY"
mkdir -p "$SITE_COPY/outputs/exports" "$SITE_COPY/outputs/reports" "$SITE_COPY/outputs/renders"
cp example_outputs/babylon_preview/scene_manifest.json "$SITE_COPY/outputs/exports/scene_manifest.json"
cp example_outputs/babylon_preview/evidence_register.csv "$SITE_COPY/outputs/exports/evidence_register.csv"
cp example_outputs/babylon_preview/validation.json "$SITE_COPY/outputs/reports/validation.json"
cp example_outputs/babylon_preview/report.html "$SITE_COPY/outputs/reports/index.html"
cp example_outputs/babylon_preview/beauty.png "$SITE_COPY/outputs/renders/beauty.png"
```

Copy `candidate-512x288.png` to a separate candidate path only for the size-normalization case. Generate each finish request during the test; do not reuse a request, provenance record, or final image.

This is pipeline demonstration output, not a completed archaeological reconstruction.
