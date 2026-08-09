# Source files

Place licensed or public-domain source files here. Add a sidecar named either `filename.ext.source.yaml` or `filename.source.yaml` when you need to control the source ID and metadata.

Example:

```yaml
id: SRC-KOLDEWEY-1914
title: The Excavations at Babylon
authors: Robert Koldewey
publication_year: 1914
source_type: excavation_report
license: Public domain
notes: Scanned edition used for page-level claim review.
```

Run `archaeoforge ingest projects/babylon_570_bce --render-visual-pages` after adding files. Do not distribute copyrighted source files unless the license permits it.
