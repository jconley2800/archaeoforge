# ArchaeoForge Codex plugin

ArchaeoForge is an evidence-controlled workflow for reconstructing historical sites from reviewed sources, dated geometry, deterministic scene manifests, Blender renders, and explicitly non-authoritative image generation.

This skills-only Codex plugin teaches Codex how to:

- initialize and diagnose ArchaeoForge projects;
- ingest sources and preserve evidence review boundaries;
- validate, compile, build, render, and report a dated site state;
- choose between strict `precise_object_edit` finishing and broad `historical_scene` interpretation;
- use Codex image generation through a separate candidate file; and
- publish the candidate only through ArchaeoForge's hash-bound registration and provenance gate.

## Requirements

- Codex with plugin and image-generation support.
- A Linux environment for the supported end-to-end workflow. The bundled inspector is portable Python, but ArchaeoForge's installer, Blender automation, and documented production path are currently Linux-first.
- ArchaeoForge CLI 0.1.0 or a matching source checkout, either on `PATH` or supplied to the bundled inspector.
- Python 3.11 or newer for ArchaeoForge.
- Blender for scene generation and rendering.
- GDAL or QGIS only when georeferencing source plans.
- An OpenAI API key only for optional API extraction, unattended finishing, or strict geometry auditing. The normal interactive Codex image-generation handoff does not require a project API key.

## Install from the public repository

After the public repository is available:

```bash
codex plugin marketplace add jconley2800/archaeoforge
codex plugin add archaeoforge@archaeoforge
```

Start a new Codex conversation and invoke:

```text
Use $archaeoforge-reconstruction to build an evidence-controlled reconstruction of Hattusa around 1300 BCE and create a lifelike historical scene.
```

The plugin is designed for Codex and does not provide a standalone graphical application. Repository installs execute local shell commands and can read or change files only within the permissions granted to Codex.

## Trust boundary

The reviewed evidence register and deterministic scene manifest are authoritative. Blender renders and generated images are derived presentation products. The plugin does not approve AI-extracted claims, select disputed hypotheses, or turn visually persuasive pixels into archaeological evidence.

Historical-scene generation deliberately allows a schematic model to become a lifelike setting while preserving named broad relationships. It is reviewed for historical plausibility, not strict pixel geometry. Precise-object editing preserves camera, crop, silhouettes, openings, roads, waterways, and object placement and may use a strict geometry audit.

## Data handling

The plugin has no hosted service and no telemetry. It directs Codex to read and write local project files. Optional OpenAI-backed operations are initiated by the user and are governed by the user's OpenAI account and project configuration. See [PRIVACY.md](PRIVACY.md).

## Support and policies

- Support: <https://github.com/jconley2800/archaeoforge/issues>
- Privacy: [PRIVACY.md](PRIVACY.md)
- Terms: [TERMS.md](TERMS.md)
- Security: [SECURITY.md](SECURITY.md)
- Third-party notices: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
- License: [MIT](LICENSE)
