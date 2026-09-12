# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A single Home Assistant Blueprint (`PV-Ladesteuerung.yaml`) for PV, battery, and heat-pump charge orchestration, driven by Solcast forecasts and dynamic take-rates. There is no application code, build system, or package manager — this repository's only deliverable is a large YAML automation consumed directly by Home Assistant.

## Repository layout

- `PV-Ladesteuerung.yaml` — the blueprint itself (`domain: automation`, `mode: queued`). This is the only file under active development.
- `pv-steuerung.yaml_` — companion Home Assistant package, a *template*: every helper the blueprint's inputs expect (`input_boolean`, `input_select`, `input_number`, `input_text`, `timer`, `utility_meter`, the intervention `binary_sensor` and its `history_stats`). Two `<HIER EINTRAGEN>` placeholders must be filled per site. Not a blueprint itself, not auto-loaded by Home Assistant under this filename.
- `tests/` — `ha_jinja.py` renders the blueprint's `variables:` chain outside Home Assistant; `test_struktur.py` holds the mechanical checks, `test_rechnung.py` the computation regressions, `test_aufzeichnung.py` the recording round-trip. `.github/workflows/check.yml` runs them.
- `tests/fixtures/*.jsonl` — lines written by the blueprint's diagnostic recording (one per half-hour run, all input values incl. the Solcast forecast the run saw). `szenario_aus_aufzeichnung` in `conftest.py` rebuilds the run; every line is pushed through the chain by `test_aufzeichnung.py`. Setup is described in the package header.
- `CHANGELOG.md` — Keep-a-Changelog format: `## [Vx.y.z] - YYYY-MM-DD` per version (date = merge day, which is the publish day), `### Added`/`Changed`/`Removed`/`Fixed` sub-headings in that order, link references to the release compare view at the bottom, an empty `## [Unreleased]` on top. The working version's section is written with the PR and carries the expected merge date; the release workflow copies that section as the release notes, so the heading must exist before publishing (`test_changelog_hat_sektion_der_arbeitsversion` enforces it).
- `LICENSE` — Apache 2.0.

## Validating changes

`pytest` runs the mechanical part of this list (`tests/test_struktur.py`) and renders the
`variables:` chain outside Home Assistant against synthetic scenarios (`tests/test_rechnung.py`,
harness in `tests/ha_jinja.py`). GitHub Actions runs it on every push and pull request. Run it
locally before pushing: `pip install -r requirements-dev.txt && pytest -q`. A regression that came
out of a log analysis belongs in `test_rechnung.py` as a scenario with a hand-computed expected
value, so it stays fixed. The judgement calls below (value range, effect chain, does the branch
fire) are still manual. Before treating any change to `PV-Ladesteuerung.yaml` as done, check it
against this list.

**Write the release note first, then run the checks** (format under "Release notes" — note it is the one English artifact in a repo whose comments and log texts are German). Drafting it up front forces every change to be named before it is validated; a change that is hard to phrase is usually one that hasn't been thought through.

1. **YAML must parse.**
2. **Duplicate-key check with a custom loader.** `yaml.SafeLoader` silently overwrites duplicate mapping keys instead of raising — a plain `yaml.safe_load` will NOT catch this class of bug. Use a loader that raises on duplicate keys, and register a constructor for the `!input` tag first (otherwise it raises a false positive on every `!input` usage).
3. **`!input` reference check.** Every `!input xyz` used in `trigger:`/`action:` must have a matching `xyz:` defined under `blueprint.input`, and vice versa (no orphaned inputs).
4. **Jinja brace balance** on any changed template: count of `{{` must equal `}}`. Note: YAML flow-style mappings like `selector: {"entity": {"domain": "sensor"}}` contain adjacent `}}` that are NOT Jinja delimiters — exclude `selector: {...}` lines before comparing counts, or the check produces a false alarm.
5. **Version consistency.** The version tag appears in exactly two places: `blueprint.name` (line 2) and the `bp_version` variable near the top of `action:`. Both must match. Log messages interpolate `{{ bp_version }}`, so they never need separate updates.
6. **Position/order:** a variable must be defined before its first use; confirm new blocks sit where intended.
7. **Neighboring logic unaffected:** compare match counts for nearby, unrelated conditions before/after the edit.
8. **Value-range check on any formula that consumes a measurement.** A reference test with self-chosen inputs only proves the formula computes — not that those inputs ever occur. Ask: *what range does this sensor actually take at this site?* and verify it against a real log/CSV. A demand calculation once shipped mathematically correct but structurally inert: the inverter caps export at the feed-in limit, so the measured excess never reaches the values the test used. Same failure class as a fix that is syntactically valid but logically dead.
9. **Guard consistency between a timer and the branch it gates.** If a branch has entry conditions (e.g. `aktueller_soc < 99 and not heute_schon_voll`), the block that starts/refreshes its timer needs the same ones — otherwise the timer describes a state the branch can no longer enter, and its expiry logs a phantom "beendet" message.
10. **Effect-chain check before proposing a change.** A real measurement is not automatically the cause of the observed behaviour. Trace the chain to the end: does the deviation reach the decision, and is it large enough to flip it? Two proposals were built on measurements that were correct but inconsequential — a load profile off by 33 % that could not matter at 0.85 kWh remaining demand, and a 0 A write that only ever fired while the current was already 0.
11. **Prove the new mechanism actually fires.** A guard that never triggers is not a safety net, it is dead code with a comment. Enumerate the parameter range and count how often the new branch adds a case the existing logic didn't already cover — if the answer is zero across the realistic range, say so and either drop it or declare it explicitly as a guard against a future parameter change. A claim like "this closes a gap" needs the count that shows the gap existed.
12. **UI text is rendered as Markdown.** A section or input `name:` starting with `1.` or `1)` becomes an ordered list — every section is its own block, so all of them render as "1.". Number them with a separator Markdown ignores (`1 – Name`). Verify with a CommonMark parser, not python-markdown: the latter accepts `1)` as plain text while Home Assistant's renderer does not, which is how a "fix" once shipped that changed `1.` to `1)` and left the bug in place.
13. **Every log message must let the reader retrace the decision.** Quote the values the branch actually compared, not neighbouring ones that happen to be at hand. A Fall B message once printed the surplus of the entire remaining window while the branch had decided on a shortened one — the numbers then read as a contradiction ("available 5.2 kWh, needed 3.2 kWh → escalate"). After changing a condition, check whether the message still names the operands of that condition.
14. **Length check on every text you added.** Comment blocks max ~6 lines, input `description:` under 300 characters. Verify after writing, not at the next cleanup commit — the tidy-up of one release survived exactly four versions before the old style crept back in with the next new block.

## Versioning scheme (follow exactly — do not deviate without being told to)

`V<MAJOR>.<MINOR>.<PATCH>`, tracked in both `blueprint.name` and `bp_version`:

- **PATCH** — bugfixes, reordering, renames, log-text wording, comment trims. No runtime behavior change.
- **MINOR** — new function, or an intentional behavior change that's backward-compatible (existing instances keep working unconfigured).
- **MAJOR** — breaking change: new required inputs, removed/renamed inputs, or a behavior change that would misbehave on existing instances without reconfiguration.

Bumping rules:
- The working file always carries the *next*, not-yet-published version number.
- Within one unpublished cycle, do not increment the same level twice (e.g. stay at 3.0.1, don't go to 3.0.2 for a second patch in the same cycle) — instead raise the *scheme level* if the accumulated changes warrant it (patch → minor if a real behavior change joins the cycle; → major on a breaking change).
- Only bump again after being explicitly told a version was published — that starts a new cycle.
- **Every merge of a blueprint change into `main` is published right away.** Instances import the blueprint straight from `main`, so a merged change is live before any tag exists; the release only records what already runs. Merge, then trigger the workflow with the version as input, in the same step.
- **Publishing** = running the `Release` workflow (`.github/workflows/release.yml`, `workflow_dispatch` on `main`, triggerable through the GitHub API). It runs the tests, reads the version from `blueprint.name`, takes that version's section from `CHANGELOG.md` (heading `## [Vx.y.z] - date`) as the notes, and creates tag and GitHub release. It refuses when the tag exists or the section is missing. Tags cannot be pushed from a session (the credential gets 403 on `refs/tags/*`), so never try; use the workflow.
- Don't delete existing comments unasked when bumping — but don't add new history either (see "Code comments and input text").
- **Never bump the version without being asked.**
- **Release procedure, in this order** (the tests and the release workflow print reminders for the CHANGELOG part): 1. bump `blueprint.name` and `bp_version`; 2. write the `## [Vx.y.z] - YYYY-MM-DD` section in `CHANGELOG.md` with `###` categories, add the `[Vx.y.z]` compare link and move `[Unreleased]`; 3. `pytest -q`; 4. PR with the template checklist filled; 5. merge; 6. trigger `Release` with the version; 7. tell the user to re-import the blueprint and name any per-site work.

## Release notes

Every code change ships with an English, GitHub-style release note (`Fixed` / `Added` / `Changed` / `Removed` sections; plain in the PR body, as `###` sub-headings in `CHANGELOG.md`) as a delta since the last *published* version — not just the current turn's changes. Only real changes belong in it; no separate "Notes" section, and no line explaining something that wasn't changed.

**Give the reason, not the case history.** A release note and a commit message should say *what* changed and *why the mechanism needed changing* — in general terms. No dates, no measured values, no incident reports. Write the rule that now holds instead ("a brief cloud gap can lift the export over the threshold long enough to pass a 30 s debounce"). Two to four lines per change; the evidence lives in the conversation and in memory, not in the repository.

## Analysing logs, traces and CSV exports

- **Check the timezone before calling a timestamp odd.** Trace filenames are UTC, the server runs UTC+2, the logbook displays UTC+3. A blockade ending at "14:30" in the log is 13:30 on the server — exactly the configured limit, not a stray value.
- **Confirm which run a trace belongs to.** At minute 0/30 two runs fire. The `update_json` run stops inside its own block and never reaches the priority cascade (~20 steps); the controlling run is `monitoring_5min` (~99 steps, ends in the cascade). Check `last_step` and the step count first.
- **Derive the sign convention from the data, not from the name.** Battery power is negative while charging, grid power negative while exporting, so house load is `PV + battery + grid`. Verify any reconstruction against a case where the answer is known (PV must be ~0 at night).
- **Only evaluate charge current where it is actually the limit.** If the measured current clusters tightly around one value (P50 ≈ P99), the setpoint limits. Wide scatter means PV limits, and the sample says nothing about the setpoint.
- **Check the version tag in the log message** before judging behaviour — an instance may still run an older release than what is on `main`.

## Working conventions

- Prefer targeted edits over rewriting large blocks from memory — the file is large enough that blind rewrites risk silent corruption elsewhere.
- Check every runtime-behavior change against real data (a Home Assistant trace, logbook export, or CSV) where possible, not just syntax validity — this project has a documented case of a syntactically valid fix that was logically inert.
- `git diff` is the source of truth for what changed, not a prose description.
- **Ask before creating a git commit**, even when the underlying code edit was already approved. Approving a commit approves the push with it — push straight after, without asking again and without announcing it as a separate step. Only skip the push when explicitly told to.
- **Work goes to a branch and a pull request, never straight to `main`.** Fill in `.github/pull_request_template.md`; its checklist is the validation list below in short form. There is no CI, so the PR body is the only record that the checks were run.
- Do not add new blueprint `input:` fields without asking first — instances are hand-configured per site, and a new field means manual reassignment on each one.
- **Every entity input carries `default: ""`**, including the ones that are mandatory in practice. Omitting `default` makes Home Assistant render the field differently and breaks instance loading outright instead of reporting what is missing. Mandatory fields are enforced at runtime by the startup check, which names them and stops the run.
- **An optional entity input never appears as a direct `entity_id: !input x`.** The value is substituted when the automation loads, and an empty one makes Home Assistant refuse the whole automation ("expected 'all' or 'none'") before any condition runs. Optional fields go through `"{{ var_x }}"` inside a guarded branch; direct `!input` is reserved for fields the startup check lists as mandatory (`test_direkte_entity_inputs_sind_pflichtfelder` enforces the pairing).

## Code comments and input text

**Comments explain why, not what.** The code already shows what happens; a comment earns its place by naming a constraint, a trade-off, or a trap that isn't visible from the code itself.

- **No history anywhere in the repository.** No dates, no incident reports, no measured values from a single event ("belegt am …: Bedarf 3,1 > Verfügbar 3,0", "Fehlalarm um 14:38:17") — not in comments, and not in commit messages or release notes either. The code states what holds *today*, the commit states why the mechanism needed changing; the case that revealed it stays in the conversation.
- **A few lines, not a paragraph.** If the comment is longer than the code it explains, it's doing the wrong job. Prefer one sentence on the non-obvious reason over a full derivation.
- **Version markers only where they carry meaning** (e.g. an input whose semantics changed and instances must be reconfigured). Not as a running changelog.

**`description:` on a blueprint input is UI text** for whoever configures the instance: one or two sentences on what the value does and how to choose it. No internal mechanics, no version history, no derivation of the formula behind it.

**`alias:` is a label, not a sentence** — it shows up in traces, so keep it scannable.

## Log message convention

Logbook messages follow one shape:

`{{ log_kopf }} <Zweig-Name>: <Handlung>. <Entscheidung + kompakter Grund>. (<Details>)`

`log_kopf` rendert zu `[Vx.y.z · HH:MM:SS]`: Version plus Lauf-Kennung (siehe unten). `bp_version` allein steht nur noch in der Aufzeichnung.

- **Ohne Klammern — Handlung und kompakter Grund.** Was der Zweig getan hat (inkl. Ladestrom-Änderung `X A → Y A`) und die Entscheidung mit den *beiden Vergleichswerten und ihrem Operator*, z. B. `Spätester Ladebeginn 13:30 Uhr, da Bedarf 11.4 kWh < Verfügbar 12.2 kWh`.
- **In Klammern — die tragenden Zahlen, nicht der Rechenweg.** Je Vergleichswert die zwei bis drei Größen, aus denen er entsteht (`Bedarf = freie Ladekapazität 6.4 kWh von 32.2 kWh × Faktor 1.2; Verfügbar = Restüberschuss 15.9 kWh − Puffer 1.0 kWh`), keine Zwischenschritte, keine Erklärsätze, keine Zusätze wie „letzte Änderung vor X Min". Die vollständige Herleitung steht in der Diagnose-Aufzeichnung: Jeder Lauf, der ein Register ändert, schreibt dort eine Zeile (`art: entscheidung`) mit allen Eingangs- und Rechenwerten dieses Laufs. Die Verbindung ist die Lauf-Kennung: Jede Meldung beginnt mit `{{ log_kopf }}` = `[Vx.y.z · HH:MM:SS]` (Startzeit des Laufs), jede Aufzeichnungszeile desselben Laufs trägt sie als `kennung`. Logbuch und Aufzeichnung zusammen ergeben die Nachrechenbarkeit; ohne Aufzeichnung erinnert eine tägliche Diagnosezeile daran. Rein informative Zusätze mit `Informativ:` kennzeichnen und sparsam einsetzen.
- **Das ausgerechnete Ergebnis** eines Vergleichswerts zeigen (`= 11.4 kWh`), damit die Klammer zur Zahl im Satz passt.
- **Die Parameter aufnehmen, die der Zweig in seinen Conditions prüft**, außer rein logische/triviale (Monat, Uhrzeit, `is_day`) — es sei denn, so ein Parameter ist der Hauptcharakter der Meldung.
- **Bezugsgröße nennen, wenn ein Wert sonst mehrdeutig ist** (z. B. `freie Ladekapazität 6.4 kWh von 32.2 kWh` klärt, worauf sich „frei" bezieht). Fallweise entscheiden.
- **Keine Prosa / kein Füllwerk**: keine erklärenden Sätze („die Sonne liefert später genug"), keine Querverweise („konsistent mit Fall B"), keine doppelte Wertnennung, keine redundanten Zusätze („ab nächster Halbstunde").
- **Immer den auslösenden Grund nennen** — die *tatsächlich geprüfte* Bedingung des Zweigs (z. B. „da der WP-Anlauf-Timer läuft"), nicht eine vorgelagerte Ursache und nicht weglassen. Handlung ohne „warum" ist unvollständig.
- **Beide Vergleichswerte belegen** — nicht nur eine Seite. Steht `A > B`, nennt die Klammer die tragenden Zahlen von `A` und von `B`.
- **Mehrdeutige Größen mit einer kurzen Beifügung eindeutig machen, konsequent in jeder Meldung**: `Nachladebedarf 0.6 kWh für Stunden mit Hausverbrauch über PV`, `freie Ladekapazität 6.4 kWh von 32.2 kWh`. Ein halber Satz, kein Erklärsatz.
- **„Informativ:" nur mit klarem Zweck** — Zusatzwerte nur, wenn sie die Meldung erklären; verwirrende oder unbeteiligte Parameter (z. B. ein Bagatell-Guard) weglassen.
- **Natürliches Deutsch, keine 1:1-Übersetzungen aus dem Englischen** — nicht „Feinsteuerung aufgegeben", „Haltephase", „Hold-Timer". So formulieren, wie es ein deutscher Muttersprachler schreiben würde; Fachbegriffe eindeutig (`Peak-Shaving`/`Lastspitzen-Kappung`, `Nachlaufzeit`, `kleinerer Wert aus … und …` statt „min aus").
- **Log-Häufigkeit NICHT verändern**: Beim Umformulieren ausschließlich den `message:`-Text ändern — nie die umgebenden `if`/`choose`-Bedingungen oder Guards. Eine Meldung darf nach der Änderung nicht öfter erscheinen als vorher und nicht neu auftauchen, wo vorher keine kam.
- **Wenig Artikel — Telegrammstil bei der Handlung.** „Ladestrom auf 200 A angehoben" statt „Der Ladestrom wird auf 200 A angehoben", sofern es sprachlich nicht holpert. Notwendige Artikel im Nebensatz bleiben.
- **Optionale Rechenweg-Teile nur zeigen, wenn sie greifen.** Ein `min()`-Deckel (z. B. Temperatur-Limit) wird nur genannt, wenn er tatsächlich limitiert (`temperatur_limit_ampere < …`), sonst weglassen — analog `temp_log_addon`. Kein Rechenweg-Ballast, der im Normalfall nichts erklärt.
- **Retry-/Versuchszähler nur bei > 1 zeigen.** `(nach N Versuchen übernommen)` nur, wenn `repeat.index > 1`; ein einzelner Versuch (Normalfall) wird nicht erwähnt.
- **Einheitliche Terminologie (Log-Text):**
  - Batterie-Ladestand → durchgängig **„Ladestand (SOC)"** (deutsches Wort, englischer Fachbegriff in Klammern beim ersten Vorkommen der Meldung), NICHT alleinstehend „SOC", „Tages-SOC", „Ziel-SOC" (→ „höchster Tages-Ladestand (SOC)", „Ziel-Ladestand").
  - Rechnerischer Schatten-BMS-Wert → **„interner Ladestand"** (nicht „interner SOC", „Interne SOC-Berechnung").
  - **Netzeinspeisung** einheitlich (nicht „Netz-Einspeise-Überschuss").
  - **„Wärmepumpe" ausschreiben** — im Log-Text kein „WP-…" (→ „Wärmepumpen-Anlaufsperre", „Wärmepumpen-Warmwasser-Boost" usw.). (Variablennamen/Alias/Kommentare bleiben unberührt.)
  - **„Cooldown"** nicht im Log-Text (englisch) → nur „Nachlauf-Timer".
- **Kein doppeltes Verb bei Mehrfach-Wertwechsel.** Bei Ziel + Hysterese nur EIN Verb (fürs Ziel: angehoben/zurückgestellt), die Hysterese als Wert in Klammer ohne eigenes Verb (nicht „…angehoben (Hysterese angepasst)").
- **Optionale Klammer-Zusätze: Satz-Punkt ans Ende** (nach dem `{% endif %}`), nicht in die bedingte Klammer — sonst fehlt bei aktivem Zweig der Schlusspunkt.
- **Keine technischen Interna.** Interne Modus-/Statusnamen (`fall_b_max`, Registerbezeichner o. Ä.), Zählwerke der Rechnung („19 von 22 Zeitfenstern", „Zeitverteilung", „JSON", „Helfer-Entität") gehören nicht in die Meldung. Was der Nutzer nicht einstellen oder beobachten kann, nützt ihm im Logbuch nichts.
- **Nichts loggen, was ohnehin immer gilt.** Wenn eine Meldung nur im Änderungsfall geschrieben wird, nicht zusätzlich „Änderungsfilter passiert" schreiben — das ist per Definition erfüllt.
- **Änderungsbeträge mit Vorzeichen** (`+34.0 A`, `−20.0 A`), damit die Richtung erkennbar ist; überall dort, wo eine Differenz genannt wird.
- **Wertwechsel als „von X auf Y" ausschreiben** (`von 54.400 V auf 56.000 V angehoben`, `Ladestrom von 8 A auf 0 A`), nicht als `X → Y`, wenn es in einem Fließtext-Satz mit Verb (angehoben/gesetzt/gedrosselt/zurückgesetzt/gestoppt) steht. Keine doppelte Nennung des Zielwerts (nicht „auf 200 A angehoben (0 A → 200 A)").
- **Gleiche Min/Max-Werte zusammenfassen:** Bei einer Spanne, deren Grenzen gleich sind, nur EINEN Wert nennen (`vorher 30 %`), nicht `30–30 %`; sind sie ungleich, `vorher uneinheitlich`.
- Einheiten mit Leerzeichen (`11.4 kWh`, `8 A`), `0 A` (nicht „0A").

## Blueprint architecture

- `mode: queued`, single automation, `choose:`-based priority cascade in the main action block: the first matching branch wins, and branch position determines precedence. Priorities are numbered in-code and kept contiguous; renumbering alone is a patch-level, non-behavioral change.
- A separate, unnumbered branch group handles heat-pump hot-water boost (start/end/watchdog). Convention: only *initiating* steps get a priority number; cleanup/watchdog/end steps are named but not numbered, since they don't compete with other branches for precedence.
- SoC is derived internally from a "shadow BMS" — cumulative charge/discharge counters rather than trusting the inverter's own SoC — but that calculation lives in a companion Home Assistant sensor *outside* this blueprint. The blueprint only ever writes tare/calibration helper values; it does not own the SoC formula.
- The diagnostic recording writes to the fixed entity id `notify.pv_optimizer_aufzeichnung` and does nothing if it does not exist. It sits after the variable chain and records inputs (`entitaeten`, `konfiguration`) plus the run's decision values (`rechnung`); new decision variables worth analysing belong in that block. The `update_json` run adds a short slot line (`art: slot`) with the half hour's house consumption, hold state and register floor, because that counter is zeroed before the monitoring run can see it; the fixture loader keeps only lines with `entitaeten`. The hourly `optimizer_vergleich` trigger (minute 12) runs the chain, posts the evcc-optimizer request through `rest_command.pv_optimizer_zweitmeinung` from the package when the URL input is set, writes an `optimizer` line (its schedule next to the blueprint's plan values) and stops before any register is touched; it is diagnosis only. This is the one deliberately hard-wired entity: opting in means creating the entity, not reconfiguring instances. Every new entity input must be added to the recording's entity list (`test_aufzeichnung_erfasst_jeden_input` enforces it). Multi-select entity inputs (`multiple: true`, `default: []`) arrive as lists; normalise them once (`wallbox_liste`, `ev_liste`) and record one entry per element.
- **Discharge planning** owns the six ToU minimum-SOC registers whenever the "Solcast: Prognose morgen" input is assigned: `f_soc` is derived every run from the cumulative net balance of three forecast days (`prognose_tage`, `b_stern_pct`; the first day is today between midnight and sunrise, tomorrow otherwise) with the rule `max(target − B*, min(reserve, 100 − B*))`, rounded down to a 5 % step, clamped to the current SOC (hold, exact) and the general minimum. It is written only when it can act (`tou_schreiben`: change of at least 5 points, SOC within `tou_schreib_vorlauf` points of the floor, hold immediately). Nothing else writes the ToU registers; without the input they stay at the general minimum. The night side is accounted in the optional helper "Netzbezug während Haltephase (kWh)": grid import while the floor holds is summed per half hour in the `update_json` run (capped at the energy held back), and at sunset the smaller of that sum and the day's export is booked to the storage-loss counter, or a "zu mutig" diagnosis is logged when the battery did not reach full. The cap is the whole day's export, not the export after reaching full: with a lower morning SOC the throttled charge would have absorbed it. The blueprint never charges from the grid: cell balancing is reached through PV by raising the planning target to 100 % when it is overdue.
- **The battery should reach full as late as the forecast allows**, not stand at 100 % for hours: the Prio 7 top-up is always throttled to "just enough by sunset minus lead time". The **morning blockade** on top of that exists for peak shaving only and runs when `spitze_erwartet` is true: the highest forecast slot minus that slot's house load reaches 90 % of the peak-shaving threshold (`peak_erwartung_anteil`). Otherwise the throttled charge starts with the first sun.
- **Temperature correction of the consumption profile (planned, not built).** The optional outdoor-temperature sensor and the optional helper "Temperaturprofil (JSON)" only record: the `update_json` run learns the mean outdoor temperature per half hour (EMA 1/7, stored as integer tenths of a degree so 48 values fit the 255-character helper) and the slot line carries measurement and mean; the optional multi-select of `weather` entities adds a `wetter` line per half hour with each source's 24-hour temperature forecast, so the forecast source is chosen from measured error, not by reputation. Reference model is evcc's heating correction: `load × (21 °C − T_forecast) / (21 °C − T_mean_same_hour)`, clamped to 0.5–2.0, skipped at forecast temperatures of 18 °C and above. Build it only after cold weeks are recorded and the slot lines show how far profile and measurement drift apart (rule 11); the evening total for the discharge planning is the place where it would matter.
- Several `template` triggers exist purely to catch fast-moving conditions (grid export spikes, cell voltage thresholds) between the regular 5-minute polling cycle — don't assume every code path only runs on the 5-minute tick.
