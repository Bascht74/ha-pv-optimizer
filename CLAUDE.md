# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A single Home Assistant Blueprint (`PV-Ladesteuerung.yaml`) for PV, battery, and heat-pump charge orchestration, driven by Solcast forecasts and dynamic take-rates. There is no application code, build system, or package manager — this repository's only deliverable is a large YAML automation consumed directly by Home Assistant.

## Repository layout

- `PV-Ladesteuerung.yaml` — the blueprint itself (`domain: automation`, `mode: queued`). This is the only file under active development.
- `pv-steuerung.yaml_` — companion Home Assistant package (helpers referenced by the blueprint's inputs: `input_boolean`, `input_number`, `input_text`, `timer`, template sensors, `utility_meter`). Not a blueprint itself, not auto-loaded by Home Assistant under this filename.
- `LICENSE` — Apache 2.0.

## Validating changes

There is no CI and no test framework. Before treating any change to `PV-Ladesteuerung.yaml` as done, check it against this list:

1. **YAML must parse.**
2. **Duplicate-key check with a custom loader.** `yaml.SafeLoader` silently overwrites duplicate mapping keys instead of raising — a plain `yaml.safe_load` will NOT catch this class of bug. Use a loader that raises on duplicate keys, and register a constructor for the `!input` tag first (otherwise it raises a false positive on every `!input` usage).
3. **`!input` reference check.** Every `!input xyz` used in `trigger:`/`action:` must have a matching `xyz:` defined under `blueprint.input`, and vice versa (no orphaned inputs).
4. **Jinja brace balance** on any changed template: count of `{{` must equal `}}`. Note: YAML flow-style mappings like `selector: {"entity": {"domain": "sensor"}}` contain adjacent `}}` that are NOT Jinja delimiters — exclude `selector: {...}` lines before comparing counts, or the check produces a false alarm.
5. **Version consistency.** The version tag appears in exactly two places: `blueprint.name` (line 2) and the `bp_version` variable near the top of `action:`. Both must match. Log messages interpolate `{{ bp_version }}`, so they never need separate updates.
6. **Position/order:** a variable must be defined before its first use; confirm new blocks sit where intended.
7. **Neighboring logic unaffected:** compare match counts for nearby, unrelated conditions before/after the edit.

## Versioning scheme (follow exactly — do not deviate without being told to)

`V<MAJOR>.<MINOR>.<PATCH>`, tracked in both `blueprint.name` and `bp_version`:

- **PATCH** — bugfixes, reordering, renames, log-text wording, comment trims. No runtime behavior change.
- **MINOR** — new function, or an intentional behavior change that's backward-compatible (existing instances keep working unconfigured).
- **MAJOR** — breaking change: new required inputs, removed/renamed inputs, or a behavior change that would misbehave on existing instances without reconfiguration.

Bumping rules:
- The working file always carries the *next*, not-yet-published version number.
- Within one unpublished cycle, do not increment the same level twice (e.g. stay at 3.0.1, don't go to 3.0.2 for a second patch in the same cycle) — instead raise the *scheme level* if the accumulated changes warrant it (patch → minor if a real behavior change joins the cycle; → major on a breaking change).
- Only bump again after being explicitly told a version was published — that starts a new cycle.
- Keep historical version comments in the code when bumping, unless the bump is explicitly about trimming them.
- **Never bump the version without being asked.**

## Release notes

Every code change ships with an English, GitHub-style release note (`Fixed` / `Added` / `Changed` / `Removed` sections, no markdown headers) as a delta since the last *published* version — not just the current turn's changes. Only real changes belong in it; no separate "Notes" section, and no line explaining something that wasn't changed.

## Working conventions

- Prefer targeted edits over rewriting large blocks from memory — the file is large enough that blind rewrites risk silent corruption elsewhere.
- Check every runtime-behavior change against real data (a Home Assistant trace, logbook export, or CSV) where possible, not just syntax validity — this project has a documented case of a syntactically valid fix that was logically inert.
- `git diff` is the source of truth for what changed, not a prose description.
- **Ask before creating a git commit**, even when the underlying code edit was already approved. Ask again, separately, before pushing.
- Do not add new blueprint `input:` fields without asking first — instances are hand-configured per site, and a new field means manual reassignment on each one.

## Blueprint architecture

- `mode: queued`, single automation, `choose:`-based priority cascade in the main action block: the first matching branch wins, and branch position determines precedence. Priorities are numbered in-code and kept contiguous; renumbering alone is a patch-level, non-behavioral change.
- A separate, unnumbered branch group handles heat-pump hot-water boost (start/end/watchdog). Convention: only *initiating* steps get a priority number; cleanup/watchdog/end steps are named but not numbered, since they don't compete with other branches for precedence.
- SoC is derived internally from a "shadow BMS" — cumulative charge/discharge counters rather than trusting the inverter's own SoC — but that calculation lives in a companion Home Assistant sensor *outside* this blueprint. The blueprint only ever writes tare/calibration helper values; it does not own the SoC formula.
- Several `template` triggers exist purely to catch fast-moving conditions (grid export spikes, cell voltage thresholds) between the regular 5-minute polling cycle — don't assume every code path only runs on the 5-minute tick.
