# ha-pv-optimizer

Home-Assistant-Blueprint für PV-Speicher mit Deye-Wechselrichter: Ladestrom nach Solcast-Prognose,
Lastspitzen-Kappung, Zellausgleich, Wärmepumpen-Warmwasser-Boost und eine Entlade-Planung, die
nachts nur so tief entlädt, wie die nächsten Tage wieder auffüllen. Eine Automation je Standort,
keine Zusatz-Software.

## Was der Blueprint tut

- **Gedrosselt voll werden.** Der Ladestrom wird so gewählt, dass die Batterie erst kurz vor
  Sonnenuntergang voll ist (Ladevorlauf einstellbar). Reicht die Prognose nicht, schaltet
  „Fall B" auf vollen Strom. Erwartet die Prognose eine Einspeisespitze über der Kappungsschwelle,
  hält eine Morgen-Blockade Platz dafür frei.
- **Lastspitzen kappen.** Überschreitet die Netzeinspeisung die Schwelle, nimmt die Batterie den
  Überschuss auf; wahlweise heizt zuerst die Wärmepumpe Warmwasser.
- **Zellausgleich über PV.** Bei erreichter Zellspannung wird die Erhaltungsspannung angehoben und
  der interne Ladestand kalibriert; nie aus dem Netz.
- **Entlade-Planung.** Die sechs ToU-Register des Wechselrichters bekommen nachts eine Untergrenze aus
  der Bilanz der nächsten drei Prognosetage: so tief, dass das Ziel (90 %) morgen wieder erreicht wird,
  aber nie so hoch, dass morgen eingespeist würde, was das Haus nachts gebraucht hätte.
- **Selbstkontrolle.** Ein Zähler „Speicherverlust" sammelt Energie, die ins Netz ging, obwohl das
  Haus sie hätte nutzen können, mit der Ursache in der Meldung; Fehleinschätzungen der Prognose
  werden als Diagnose protokolliert.

## Installation

1. **Package anlegen:** `pv-steuerung.yaml_` nach `packages/` kopieren (Endung `.yaml`), die zwei
   Stellen `<HIER EINTRAGEN>` füllen, Home Assistant neu starten. Es legt alle Helfer an, die der
   Blueprint erwartet.
2. **Blueprint importieren:** Rohdatei-URL dieser Datei auf `main`
   (`https://raw.githubusercontent.com/Bascht74/ha-pv-optimizer/main/PV-Ladesteuerung.yaml`).
   Updates: Blueprint erneut importieren, GitHub liefert die Datei bis zu 5 Minuten aus dem Cache.
3. **Automation anlegen** und die Felder zuweisen. Pflichtfelder prüft der Blueprint beim Start
   selbst und nennt fehlende im Logbuch. Optionale Felder (Wärmepumpe, dritter Akku-Pack,
   Solcast-Folgetage, Diagnose-Helfer) bleiben leer, wenn nicht gebraucht.

Jeder Merge nach `main` ist sofort live; das zugehörige Release
(`V<MAJOR>.<MINOR>.<PATCH>`) dokumentiert, was läuft. Änderungen stehen in `CHANGELOG.md`.

## Logbuch und Diagnose-Aufzeichnung

Jede Entscheidung schreibt eine Logbuch-Meldung nach dem Muster

```
[V6.6.0 · 08:30:02] Prio 7 (Dynamische Ladung): Ladestrom von 200 A auf 36 A gesetzt (−164 A),
da Bedarf 12.6 kWh bis zum Ladefensterende mit 33 A + 3 A Regelabweichung gedeckt wird
(freie Ladekapazität 12.1 kWh von 20.5 kWh, Nachladebedarf 0.5 kWh für Stunden mit
Hausverbrauch über PV, Ladevorlauf 1 h).
```

Vorn stehen Version und **Lauf-Kennung** (Startzeit des Laufs auf die Sekunde), dann Handlung,
Entscheidung mit beiden Vergleichswerten und in der Klammer die Zahlen, die sie tragen.

Die vollständige Herleitung liegt in der optionalen **Diagnose-Aufzeichnung**: Existiert die
Entität `notify.pv_optimizer_aufzeichnung` (File-Integration, Einrichtung im Kopf des Package),
schreibt der Blueprint JSON-Zeilen nach `/config/www/pv_optimizer_aufzeichnung.jsonl`:

| `art` | wann | Inhalt |
|---|---|---|
| `lauf` | jede halbe Stunde | alle Eingangswerte inkl. Solcast-Prognose (`entitaeten`, `konfiguration`) und alle Rechenwerte des Laufs (`rechnung`) |
| `entscheidung` | nach jedem Lauf, der ein Register, den Modus oder einen Timer geändert hat | dasselbe, mit den Werten genau dieses Laufs |
| `slot` | jede halbe Stunde aus dem Profil-Lauf | Hausverbrauch der Halbstunde, Halte-Lage der Entlade-Untergrenze, Register |

Die `kennung` jeder Zeile ist die Lauf-Kennung aus dem Logbuch: Zur Meldung `[V6.6.0 · 08:30:02]`
gehört die Zeile mit `"kennung": "08:30:02"` und `"art": "entscheidung"`. Ohne Aufzeichnung
erinnert der Blueprint einmal täglich bei Sonnenuntergang daran, dass die Entscheidungen des Tages
nicht nachgerechnet werden können.

Die Datei ist unter `/local/pv_optimizer_aufzeichnung.jsonl` abrufbar (etwa 1 MB je Tag). Kopien
liegen als Fixtures in `tests/fixtures/`; die Tests bauen jeden aufgezeichneten Lauf nach und
rechnen ihn durch dieselbe Variablenkette.

## Entwicklung

`pip install -r requirements-dev.txt && pytest -q` rendert die Variablenkette des Blueprints
außerhalb von Home Assistant gegen synthetische Szenarien und gegen die aufgezeichneten Läufe.
Arbeitsregeln, Prüfliste und Log-Konvention stehen in `CLAUDE.md`. Lizenz: Apache 2.0.
