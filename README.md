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

1. **Package anlegen:** `pv-steuerung.yaml_` nach `packages/` kopieren (Endung `.yaml`), die
   Stellen `<HIER EINTRAGEN>` füllen, Home Assistant neu starten. Es legt alle Helfer an, die der
   Blueprint erwartet. Ohne Wallbox die mit „Nur mit Wallbox“ markierten Blöcke löschen.
2. **Blueprint importieren:** Rohdatei-URL dieser Datei auf `main`
   (`https://raw.githubusercontent.com/Bascht74/ha-pv-optimizer/main/PV-Ladesteuerung.yaml`).
   Updates: Blueprint erneut importieren, GitHub liefert die Datei bis zu 5 Minuten aus dem Cache.
3. **Automation anlegen** und die Felder zuweisen. Pflichtfelder prüft der Blueprint beim Start
   selbst und nennt fehlende im Logbuch. Optionale Felder (Wärmepumpe, dritter Akku-Pack,
   Solcast-Folgetage, Diagnose-Helfer, Wallbox) bleiben leer, wenn nicht gebraucht.

**Wallbox / evcc (Sektion 11):** Die Autos laden zuerst, der Blueprint plant die Batterie mit dem
Rest. Zwei optionale Felder mit Mehrfachauswahl aus der evcc-Integration: je Ladepunkt ein
Utility-Meter über dessen Ladeenergie (bleibt aus dem Verbrauchsprofil heraus, Vorlage im Package)
und je Ladepunkt der offene Ladebedarf „Charge Remaining Energy“ (geht vom heutigen PV-Überschuss
ab, bevor Ladefenster, Fall B und Blockade gerechnet werden). evcc selbst darf die Batterie nicht
steuern: „Entladung der Hausbatterie … verhindern“ und „Entladung ins Stromnetz zulassen“ aus.

**Zweitmeinung vom evcc-Optimizer:** Mit der Adresse des Optimizer-Add-ons im Feld „evcc-Optimizer:
Adresse“ holt der Blueprint stündlich (Minute 12) dessen Batterie-Fahrplan über `rest_command` aus dem
Package und schreibt ihn als Zeile `optimizer` in die Aufzeichnung, neben Ladebeginn, Vollzeit und
Untergrenze des Blueprints für denselben Lauf. Eingaben sind die Solcast-Prognose je Slot, das
gelernte Verbrauchsprofil, Kapazität, Ladestand und Grenzen, feste Preise (30 ct Bezug, 8 ct
Einspeisung, nur das Verhältnis zählt) und die Strategie „Einspeisespitzen glätten“, die der
Blockade plus gedrosselter Ladung entspricht. Reine Diagnose, steuert nichts.

**Außentemperatur (Sektion 3, optional):** wird je Halbstunde in die `slot`-Zeile geschrieben, damit
sich der Verbrauch nach Temperatur auswerten lässt. Steuert nichts.

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
| `lauf` | jede halbe Stunde | alle Eingangswerte inkl. Solcast-Prognose (`entitaeten`, `konfiguration`) und alle Rechenwerte des Laufs (`rechnung`), darunter die geplanten Zeiten `plan_voll_um`, `fallb_voll_um`, `untergrenze_um` und die reale Vollzeit `voll_real_um`; `halten_aktiv` markiert, ab wann die Untergrenze real hält |
| `entscheidung` | nach jedem Lauf, der ein Register, den Modus oder einen Timer geändert hat | dasselbe, mit den Werten genau dieses Laufs |
| `slot` | jede halbe Stunde aus dem Profil-Lauf | Hausverbrauch der Halbstunde (ohne Wallbox) und Wallbox-Ladung, Halte-Lage der Entlade-Untergrenze, Register |
| `optimizer` | stündlich, nur mit Optimizer-Adresse | Fahrplan des evcc-Optimizers (Ladebeginn, Vollzeit, Nacht-Minimum, Ladestand-Verlauf) neben den Werten des Blueprints für denselben Lauf |

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
