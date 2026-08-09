# ArchaeoForge — contributor agent brief

Read this before changing the evidence or Blender pipeline. The safeguards below come from
reproduced failures and are part of the project's public contributor guidance.

## What this project is

An evidence-controlled pipeline that turns archaeological sources into a reviewed evidence register,
a deterministic scene manifest, and a Blender render. The authoritative artifact is the register and
the manifest. The image is a derived product. The whole point is that a convincing picture cannot
silently mix measurement, reconstruction, analogy, and invention — so any change that lets unreviewed
material reach an authoritative build is a serious regression, not a convenience.

`README.md` is user-facing and current. `docs/ARCHITECTURE.md` maps the modules.
`docs/TEST_RESULTS.md` is the dated verification log — append to it, do not rewrite history.

## Running things

```bash
source .venv/bin/activate
pytest                                          # 102-test host and live-Blender suite
./run_babylon_demo.sh --skip-blender            # evidence + manifest + report only
archaeoforge compile projects/babylon_570_bce --preview
archaeoforge render  projects/babylon_570_bce   # needs Blender
```

`archaeoforge render` reads the **compiled manifest**, not `project.yaml`. Editing `project.yaml`
without re-running `compile` renders the old settings — this wastes a lot of time if you miss it.

Blender log lands in `projects/babylon_570_bce/.archaeoforge/logs/blender-render.log`. Grep it for
`ARCHAEOFORGE` — the script prints its camera solution and any setting the installed Blender refused.

## Environment traps

- Sandboxed Blender packages, including Flatpak builds, may have a private `/tmp`. Stage scratch
  scripts and test fixtures somewhere visible to the selected Blender executable.
- Blender engine and colour-management enum names vary by version. Probe the installed build's RNA
  and report unsupported settings instead of assuming an assignment took effect.
- Blender can silently ignore a property assignment and still produce a valid PNG. Prove render
  changes by inspecting both the log and the resulting image.
- `make lint` is clean as of the 2026-08-09 completion pass. The established `str, Enum` classes
  remain in place with targeted rationale suppressions rather than a behavior-changing migration to
  `StrEnum`.

## What was just repaired

The reported symptom was "the render looks wrong". Root cause: **camera far-clip was never set**, so
Blender's factory 1000 m plane deleted the far half of a 700x850 m site and it rendered as a truncated
wedge. Every manual re-aim clipped more, which is why 17 hand-iterated previews all failed.

Also fixed: camera auto-framing (`src/archaeoforge/framing.py`, new, pure stdlib so it imports both
inside Blender and under pytest); a real sky node tree, because `scene.world.color` is ignored on a
node-based world and there was no ambient fill; the swallowed colour-management look; `samples` and
ray tracing being applied to Cycles only; render passes named `depth_.exr` instead of `depth.exr`;
ziggurat stairs not rotating with the ziggurat.

`tests/test_framing.py` (fast, CI-safe) and `tests/test_blender_render.py` (drives real Blender, skips
when absent) cover this. The latter asserts the site covers 20–95% of the frame and that the corner
pixel is sky-blue — those two assertions are what would have caught the original bug.

## Completion pass after the original handoff

The independently reproduced defects above were repaired on 2026-08-09. Object indices are dense,
persisted, and separated from cryptomatte output; reports are autoescaped; exported handoffs redact
local absolute paths; line corners are mitred; initialization is non-destructive; configuration
rejects unknown fields; the sdist is self-testable; and `ai.finish_enabled` now controls either a
public-API finish or a pending interactive handoff.

Codex image generation is integrated through `prepare-finish` and `register-finish`, not represented
as an importable Python provider. Requests and result records bind the prompt, base render, and scene
manifest by hash. Generated imagery remains non-authoritative and review-required unless its audit or
recorded manual review accepts it. `precise_object_edit` is the strict geometry-preserving default;
`historical_scene` treats a schematic render only as a broad composition guide, skips the inapplicable
strict geometry audit, and remains non-authoritative. Only a named, unnormalized historical-
plausibility acceptance can clear its review-required flag. See `docs/TEST_RESULTS.md` for the
executed checks, retained rejection example, and live Babylon historical-scene result.

## House rules

- Do not weaken an evidence gate to make a build succeed. If the Babylon starter refuses an
  authoritative build, that is the starter behaving correctly: its claims are `needs_review` and
  several sources have no local copy.
- The Babylon geometry is deliberately schematic. Do not "improve" it toward looking like real
  Babylon; that is exactly the failure mode the project exists to prevent.
- Prove render changes with a render. Read the PNG back and look at it before claiming a fix works.
- Append to `docs/TEST_RESULTS.md` with what you actually executed, and state what you did not.
