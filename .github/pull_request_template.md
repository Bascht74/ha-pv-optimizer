<!--
Die GitHub Action prüft nur, was sich mechanisch prüfen lässt. Der Rest ist
Handarbeit — und genau deshalb steht er hier. Abschnitte, die nicht zutreffen, ersatzlos löschen.
-->

## Release note

<!--
Englisch, GitHub-Stil, als Delta seit der letzten VERÖFFENTLICHTEN Version —
nicht nur seit dem letzten Commit. Nur echte Änderungen.
Sagt WAS sich geändert hat und WARUM der Mechanismus geändert werden musste,
in allgemeiner Form: keine Daten, keine Messwerte, keine Fallgeschichten.
Zwei bis vier Zeilen je Änderung.
-->

```
Fixed
-

Added
-

Changed
-

Removed
-
```

## Version

- [ ] `blueprint.name` (Zeile 2) und `bp_version` tragen dieselbe Nummer
- **Stufe:** PATCH / MINOR / MAJOR — <!-- warum diese Stufe? -->

<!--
PATCH  Bugfix, Umsortierung, Log-Text, Kommentare — kein Verhaltenswechsel
MINOR  neue Funktion oder gewollte Verhaltensänderung, bestehende Instanzen laufen unkonfiguriert weiter
MAJOR  neue Pflichtfelder, entfernte/umbenannte Inputs, oder Verhalten, das ohne Neukonfiguration danebengreift
-->

## Beleg

<!--
Womit ist belegt, dass die Änderung greift — und dass sie nötig war?
Trace, Logbuch-Export oder CSV, nicht nur Syntax-Prüfung.

Für jede Verhaltensänderung:
- Wertebereich: Nimmt der Sensor die Werte, mit denen gerechnet wurde, an diesem
  Standort überhaupt an? Gegen echte Daten prüfen, nicht gegen selbstgewählte.
- Wirkungskette: Erreicht die Abweichung die Entscheidung, und ist sie groß genug,
  sie zu kippen?
- Neue Zweige: Wie oft feuern sie im realen Parameterbereich? Ein Zweig, der nie
  auslöst, ist kein Sicherheitsnetz, sondern toter Code.
-->

## Auswirkung auf bestehende Instanzen

- [ ] Keine neuen `input:`-Felder — oder die neuen sind hier benannt, samt der Handarbeit, die sie je Instanz kosten
- [ ] Jedes Entity-Input trägt `default: ""`
- [ ] Geänderte Log-Meldungen erscheinen nicht häufiger als vorher und nicht neu, wo vorher keine kam

## Prüfliste

- [ ] YAML parst
- [ ] Duplikat-Key-Prüfung mit einem Loader, der bei doppelten Keys wirft (`yaml.SafeLoader` überschreibt stillschweigend), Konstruktor für `!input` vorher registriert
- [ ] Jedes `!input xyz` hat ein `xyz:` unter `blueprint.input` — und umgekehrt, keine verwaisten Inputs
- [ ] Jinja-Klammerbilanz in jedem geänderten Block (`selector: {...}`-Zeilen vorher ausschließen)
- [ ] Variablen stehen vor ihrer ersten Nutzung
- [ ] Nachbarlogik unberührt: Trefferzahlen benachbarter Bedingungen vor/nach dem Edit verglichen
- [ ] Timer und der Zweig, den er absichert, tragen dieselben Eintrittsbedingungen
- [ ] Jede Log-Meldung nennt die Operanden genau der Bedingung, über die ihr Zweig entschieden hat
- [ ] Texte auf Länge geprüft: Kommentarblöcke max. ~6 Zeilen, `description:` unter 300 Zeichen
- [ ] UI-Texte mit einem CommonMark-Parser gegengelesen, falls `name:` mit einer Ziffer beginnt
- [ ] `CHANGELOG.md`: Sektion `## [Vx.y.z] - YYYY-MM-DD` der Arbeitsversion mit `### Added/Changed/Removed/Fixed` geschrieben, Link-Referenz ergänzt, `[Unreleased]` weitergestellt
