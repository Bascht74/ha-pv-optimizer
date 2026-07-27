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
8. **Value-range check on any formula that consumes a measurement.** A reference test with self-chosen inputs only proves the formula computes — not that those inputs ever occur. Ask: *what range does this sensor actually take at this site?* and verify it against a real log/CSV. A demand calculation once shipped mathematically correct but structurally inert: the inverter caps export at the feed-in limit, so the measured excess never reaches the values the test used. Same failure class as a fix that is syntactically valid but logically dead.
9. **Guard consistency between a timer and the branch it gates.** If a branch has entry conditions (e.g. `aktueller_soc < 99 and not heute_schon_voll`), the block that starts/refreshes its timer needs the same ones — otherwise the timer describes a state the branch can no longer enter, and its expiry logs a phantom "beendet" message.

## Versioning scheme (follow exactly — do not deviate without being told to)

`V<MAJOR>.<MINOR>.<PATCH>`, tracked in both `blueprint.name` and `bp_version`:

- **PATCH** — bugfixes, reordering, renames, log-text wording, comment trims. No runtime behavior change.
- **MINOR** — new function, or an intentional behavior change that's backward-compatible (existing instances keep working unconfigured).
- **MAJOR** — breaking change: new required inputs, removed/renamed inputs, or a behavior change that would misbehave on existing instances without reconfiguration.

Bumping rules:
- The working file always carries the *next*, not-yet-published version number.
- Within one unpublished cycle, do not increment the same level twice (e.g. stay at 3.0.1, don't go to 3.0.2 for a second patch in the same cycle) — instead raise the *scheme level* if the accumulated changes warrant it (patch → minor if a real behavior change joins the cycle; → major on a breaking change).
- Only bump again after being explicitly told a version was published — that starts a new cycle.
- Don't delete existing comments unasked when bumping — but don't add new history either (see "Code comments and input text").
- **Never bump the version without being asked.**

## Release notes

Every code change ships with an English, GitHub-style release note (`Fixed` / `Added` / `Changed` / `Removed` sections, no markdown headers) as a delta since the last *published* version — not just the current turn's changes. Only real changes belong in it; no separate "Notes" section, and no line explaining something that wasn't changed.

**Give the reason, not the case history.** A release note and a commit message should say *what* changed and *why the mechanism needed changing* — in general terms. No dates, no measured values, no incident reports. Write the rule that now holds instead ("a brief cloud gap can lift the export over the threshold long enough to pass a 30 s debounce"). Two to four lines per change; the evidence lives in the conversation and in memory, not in the repository.

## Working conventions

- Prefer targeted edits over rewriting large blocks from memory — the file is large enough that blind rewrites risk silent corruption elsewhere.
- Check every runtime-behavior change against real data (a Home Assistant trace, logbook export, or CSV) where possible, not just syntax validity — this project has a documented case of a syntactically valid fix that was logically inert.
- `git diff` is the source of truth for what changed, not a prose description.
- **Ask before creating a git commit**, even when the underlying code edit was already approved. Ask again, separately, before pushing.
- Do not add new blueprint `input:` fields without asking first — instances are hand-configured per site, and a new field means manual reassignment on each one.

## Code comments and input text

**Comments explain why, not what.** The code already shows what happens; a comment earns its place by naming a constraint, a trade-off, or a trap that isn't visible from the code itself.

- **No history anywhere in the repository.** No dates, no incident reports, no measured values from a single event ("belegt am …: Bedarf 3,1 > Verfügbar 3,0", "Fehlalarm um 14:38:17") — not in comments, and not in commit messages or release notes either. The code states what holds *today*, the commit states why the mechanism needed changing; the case that revealed it stays in the conversation.
- **A few lines, not a paragraph.** If the comment is longer than the code it explains, it's doing the wrong job. Prefer one sentence on the non-obvious reason over a full derivation.
- **Version markers only where they carry meaning** (e.g. an input whose semantics changed and instances must be reconfigured). Not as a running changelog.

**`description:` on a blueprint input is UI text** for whoever configures the instance: one or two sentences on what the value does and how to choose it. No internal mechanics, no version history, no derivation of the formula behind it.

**`alias:` is a label, not a sentence** — it shows up in traces, so keep it scannable.

## Log message convention

Logbook messages follow one shape:

`{{ bp_version }} <Zweig-Name>: <Handlung>. <Entscheidung + kompakter Grund>. (<Details>)`

- **Ohne Klammern — Handlung und kompakter Grund.** Was der Zweig getan hat (inkl. Ladestrom-Änderung `X A → Y A`) und die Entscheidung mit den *beiden Vergleichswerten und ihrem Operator*, z. B. `Spätester Ladebeginn 13:30 Uhr, da Bedarf 11.4 kWh < Verfügbar 12.2 kWh`.
- **In Klammern — die vollständige Herleitung.** Je Wert der Rechenweg mit Variablen und Ergebnis (`Bedarf = freie Ladekapazität 6.4 kWh von 32.2 kWh × Sicherheitsfaktor 1.1 × …`). Rein informative Zusätze mit `Informativ:` kennzeichnen.
- **Immer das ausgerechnete Ergebnis** einer Berechnung zeigen (`= 11.4 kWh`), nicht nur die Faktoren, und den Ausgangswert vor Abzügen (`Prognose 13.2 kWh − Puffer 1.0 kWh`) — die Zahlen sollen nachrechenbar sein.
- **Die Parameter aufnehmen, die der Zweig in seinen Conditions prüft**, außer rein logische/triviale (Monat, Uhrzeit, `is_day`) — es sei denn, so ein Parameter ist der Hauptcharakter der Meldung.
- **Bezugsgröße nennen, wenn ein Wert sonst mehrdeutig ist** (z. B. `freie Ladekapazität 6.4 kWh von 32.2 kWh` klärt, worauf sich „frei" bezieht). Fallweise entscheiden.
- **Keine Prosa / kein Füllwerk**: keine erklärenden Sätze („die Sonne liefert später genug"), keine Querverweise („konsistent mit Fall B"), keine doppelte Wertnennung, keine redundanten Zusätze („ab nächster Halbstunde").
- **Immer den auslösenden Grund nennen** — die *tatsächlich geprüfte* Bedingung des Zweigs (z. B. „da der WP-Anlauf-Timer läuft"), nicht eine vorgelagerte Ursache und nicht weglassen. Handlung ohne „warum" ist unvollständig.
- **Beide Vergleichswerte mit vollem Rechenweg** — nicht nur eine Seite herleiten. Steht `A > B`, gehört sowohl die Herleitung von `A` als auch die von `B` in die Klammer (inkl. Ausgangswert vor Abzügen).
- **Mehrdeutige Größen im Klartext erklären — und zwar KONSEQUENT in JEDER Meldung, in der sie vorkommen**, nicht nur einmal. Z. B. `Nachladebedarf … für Phasen, in denen der Hausverbrauch die PV übersteigt`, `freie Ladekapazität … von … kWh gesamt`. Lieber ein Wort mehr als eine unklare Variable; die Erklärung NICHT beim Kürzen entfernen.
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
- **Keine technischen Interna.** Interne Modus-/Statusnamen (`fall_b_max`, Registerbezeichner o. Ä.) gehören nicht in die Meldung.
- **Nichts loggen, was ohnehin immer gilt.** Wenn eine Meldung nur im Änderungsfall geschrieben wird, nicht zusätzlich „Änderungsfilter passiert" schreiben — das ist per Definition erfüllt.
- **Änderungsbeträge mit Vorzeichen** (`+34.0 A`, `−20.0 A`), damit die Richtung erkennbar ist; überall dort, wo eine Differenz genannt wird.
- **Wertwechsel als „von X auf Y" ausschreiben** (`von 54.400 V auf 56.000 V angehoben`, `Ladestrom von 8 A auf 0 A`), nicht als `X → Y`, wenn es in einem Fließtext-Satz mit Verb (angehoben/gesetzt/gedrosselt/zurückgesetzt/gestoppt) steht. Keine doppelte Nennung des Zielwerts (nicht „auf 200 A angehoben (0 A → 200 A)").
- **Gleiche Min/Max-Werte zusammenfassen:** Bei einer Spanne, deren Grenzen gleich sind, nur EINEN Wert nennen (`vorher 30 %`), nicht `30–30 %`; sind sie ungleich, `vorher uneinheitlich`.
- Einheiten mit Leerzeichen (`11.4 kWh`, `8 A`), `0 A` (nicht „0A").

## Blueprint architecture

- `mode: queued`, single automation, `choose:`-based priority cascade in the main action block: the first matching branch wins, and branch position determines precedence. Priorities are numbered in-code and kept contiguous; renumbering alone is a patch-level, non-behavioral change.
- A separate, unnumbered branch group handles heat-pump hot-water boost (start/end/watchdog). Convention: only *initiating* steps get a priority number; cleanup/watchdog/end steps are named but not numbered, since they don't compete with other branches for precedence.
- SoC is derived internally from a "shadow BMS" — cumulative charge/discharge counters rather than trusting the inverter's own SoC — but that calculation lives in a companion Home Assistant sensor *outside* this blueprint. The blueprint only ever writes tare/calibration helper values; it does not own the SoC formula.
- Several `template` triggers exist purely to catch fast-moving conditions (grid export spikes, cell voltage thresholds) between the regular 5-minute polling cycle — don't assume every code path only runs on the 5-minute tick.
