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
- **Zellausgleich über PV.** Bei erreichter Zellspannung wird der Ladestrom auf die Ausgleichs-Rate
  gedrosselt, damit die Zellen oben genug Zeit zum Ausgleichen bekommen; nie aus dem Netz.
  Die Erhaltungsspannung stellt der Wechselrichter selbst, der Blueprint schreibt keine Spannung.
- **Entlade-Planung.** Die sechs ToU-Register des Wechselrichters bekommen nachts eine Untergrenze aus
  der Halbstunden-Bilanz der nächsten 72 Stunden: so tief, dass das Ziel (90 %) am Ende des besten
  Sonnentags wieder erreicht wird, aber nie so hoch, dass eingespeist würde, was das Haus nachts
  gebraucht hätte. Das Vertrauen in die Prognose hängt am Vorlauf in Stunden, nicht am Kalendertag,
  deshalb springt nichts um Mitternacht.
- **Selbstkontrolle.** Ein Zähler „Speicherverlust" sammelt Energie, die ins Netz ging, obwohl das
  Haus sie hätte nutzen können, mit der Ursache in der Meldung; Fehleinschätzungen der Prognose
  werden als Diagnose protokolliert. Der Fall, in dem wirklich Ertrag verloren geht — der
  Wechselrichter regelt ab, weil die Einspeisung über der Schwelle liegt und der Strom nicht mehr
  in die volle Batterie passt —, bekommt eine eigene Meldung.

## Installation

1. **Package anlegen:** `pv-steuerung.yaml_` nach `packages/` kopieren (Endung `.yaml`), die
   Stellen `<HIER EINTRAGEN>` füllen, Home Assistant neu starten. Es legt alle Helfer an, die der
   Blueprint erwartet. Die optionalen Blöcke (Wallbox, Notstromkreis) sind auskommentiert und
   werden nur einkommentiert, wenn sie gebraucht werden — ein stehengebliebener Platzhalter
   verwirft die ganze Gruppe, samt des Hausverbrauchszählers daneben.
2. **Blueprint importieren:** Rohdatei-URL dieser Datei auf `main`
   (`https://raw.githubusercontent.com/Bascht74/ha-pv-optimizer/main/PV-Ladesteuerung.yaml`).
   Updates: Blueprint erneut importieren, GitHub liefert die Datei bis zu 5 Minuten aus dem Cache.
3. **Automation anlegen** und die Felder zuweisen. Pflichtfelder prüft der Blueprint beim Start
   selbst und nennt fehlende im Logbuch. Optionale Felder (Wärmepumpe, zweiter und dritter
   Akku-Pack, Solcast-Folgetage, Diagnose-Helfer, Wallbox) bleiben leer, wenn nicht gebraucht.

**Ladestand (Sektion 1):** Der Blueprint nimmt den Ladestand als Messwert und rechnet ihn nicht
selbst aus. In „Ladezustand SOC (%)" gehört der Wert, den der Wechselrichter selbst sieht — er prüft
seine ToU-Register gegen sein eigenes BMS. Wer dem nicht traut, kann ein „Schatten-BMS" daneben
stellen, das den Ladestand aus den Lade- und Entladezählern bildet und seinen Nullpunkt setzt,
sobald die Zellspannung die Batterie als voll ausweist. Es läuft außerhalb dieses Repositories,
etwa auf dem Batteriemonitor selbst (ESPHome); Home Assistant braucht davon nur den Ladestand-Sensor,
und der kommt in das optionale Feld darunter. Ist es gesetzt, rechnet der Blueprint durchgehend in
dessen Skala und rechnet die Untergrenze erst beim Schreiben in die Skala des Wechselrichters um — der
kennt nur sein eigenes BMS. Den Wechselrichter stellt in jedem Fall nur dieser Blueprint.

**Wallbox / evcc (Sektion 11):** Die Autos laden zuerst, der Blueprint plant die Batterie mit dem
Rest. Zwei optionale Felder mit Mehrfachauswahl aus der evcc-Integration: je Ladepunkt ein
Utility-Meter über dessen Ladeenergie (bleibt aus dem Verbrauchsprofil heraus, Vorlage im Package)
und je Ladepunkt der offene Ladebedarf „Charge Remaining Energy“ (geht vom heutigen PV-Überschuss
ab, bevor Ladefenster, Fall B und Blockade gerechnet werden). evcc selbst darf die Batterie nicht
steuern: „Entladung der Hausbatterie … verhindern“ und „Entladung ins Stromnetz zulassen“ aus.

**Entlade-Untergrenze und Wechselrichter:** Der Blueprint schreibt die Untergrenze in die sechs
ToU-Register des Deye. Fällt der Ladestand nachts trotzdem mehr als drei Punkte darunter, während die
Batterie entlädt, hält der Wechselrichter die Grenze nicht. Häufigste Ursache beim Deye: „Battery
Operation Mode“ steht auf „Voltage“, dann rechnet er die Programme mit den Spannungsfeldern und
ignoriert die SOC-Felder; auf „Capacity“ stellen, dazu „Charging“ der Programme auf „Disabled“.
Weitere Ursache: Zeitsteuerung aus. Weicht seine eigene Ladestand-Anzeige vom genaueren Sensor ab,
ist dafür das Schatten-BMS-Feld da — dann trifft die Grenze wieder den gemeinten Ladestand. Das
Logbuch meldet das einmal je Nacht; die Grenze wird dann nicht nachgezogen.

**Zweitmeinung vom evcc-Optimizer:** Mit der Adresse des Optimizer-Add-ons im Feld „evcc-Optimizer:
Adresse“ holt der Blueprint stündlich (Minute 12) dessen Batterie-Fahrplan über `rest_command` aus dem
Package und schreibt ihn als Zeile `optimizer` in die Aufzeichnung, neben Ladebeginn, Vollzeit und
Untergrenze des Blueprints für denselben Lauf. Eingaben sind die Solcast-Prognose je Slot, das
gelernte Verbrauchsprofil, Kapazität, Ladestand und Grenzen, feste Preise (30 ct Bezug, 8 ct
Einspeisung, nur das Verhältnis zählt) und die Strategie „Einspeisespitzen glätten“, die der
Blockade plus gedrosselter Ladung entspricht. Reine Diagnose, steuert nichts.

**Außentemperatur (Sektion 3, optional):** wird je Halbstunde in die `slot`-Zeile geschrieben, damit
sich der Verbrauch nach Temperatur auswerten lässt. Mit dem optionalen Helfer „Temperaturprofil
(JSON)“ lernt der Blueprint dazu die mittlere Temperatur je Halbstunde, Vorbereitung für eine
spätere Temperaturkorrektur des Verbrauchsprofils. Im Feld „Wetter-Prognose Temperatur“ lassen sich
mehrere Wetter-Entitäten wählen (Open-Meteo, DWD, Met.no); ihre Stundenprognose der nächsten 24 Stunden
landet je Halbstunde in einer Zeile `wetter`, damit sich die genaueste Quelle für den Standort gegen den
Außenfühler bestimmen lässt. Alles steuert nichts.

**Notstromkreis (Sektion 3 und 5, optional):** Der Deye liefert am Notstromausgang (Load UPS) nur eine
Leistung; das Package bildet daraus per Riemann-Sensor die Energie und zählt sie je Halbstunde. Der
Blueprint schreibt den Wert in die `slot`-Zeile und lernt im Helfer „Notstromprofil (JSON)“ das
Profil in Wattstunden je Halbstunde, ein Startwert aus der Historie kann eingetragen werden. Hängt nur
ein Teil des Hauses am Notstromausgang, zählt nur dieser Teil. Mit zugewiesenem Profil hält die
Entlade-Planung eine **Notstromreserve**: Ohne Netz gelten die ToU-Programme nicht, der Deye entlädt im
Inselbetrieb bis zu seinem „Battery Shutdown SOC“ (Sektion 2, optional; leer: 10 %). Die Untergrenze
liegt deshalb nie unter diesem Ladestand plus dem größten Defizit der Notstromlast (samt Eigenverbrauch
des Wechselrichters) gegen die P10-Prognose über den eingestellten Zeitraum (Sektion 10, Vorgabe 48 Stunden),
auf volle 5 % aufgerundet. Die
Meldung der Untergrenze nennt Reserve und Defizit, die Aufzeichnung trägt `reserve_kwh`, `reserve_pct`
und `reserve_bis`.

## Logbuch und Diagnose-Aufzeichnung

Jede Entscheidung schreibt eine Logbuch-Meldung nach dem Muster

```
[V6.16.0 · 08:30:02] Prio 7 (Dynamische Ladung): Ladestrom von 200 A auf 36 A gesetzt (−164 A),
da Bedarf 12.6 kWh mit 33 A + 3 A Regelabweichung bis 1 h vor Ende des Ladefensters gedeckt
wird (freie Ladekapazität 12.1 kWh von 20.5 kWh, Nachladebedarf 0.5 kWh für Stunden mit
Hausverbrauch über PV).
```

Vorn stehen Version und **Lauf-Kennung** (Startzeit des Laufs auf die Sekunde), dann Handlung,
Entscheidung mit beiden Vergleichswerten und in der Klammer die Zahlen, die sie tragen.

Die vollständige Herleitung liegt in der optionalen **Diagnose-Aufzeichnung**: Existiert die
Entität `notify.pv_optimizer_aufzeichnung` (File-Integration, Einrichtung im Kopf des Package),
schreibt der Blueprint JSON-Zeilen nach `/config/pv_optimizer/aufzeichnung.jsonl`:

| `art` | wann | Inhalt |
|---|---|---|
| `lauf` | jede halbe Stunde | alle Eingangswerte inkl. Solcast-Prognose (`entitaeten`, `konfiguration`) und alle Rechenwerte des Laufs (`rechnung`), darunter die geplanten Zeiten `plan_voll_um`, `fallb_voll_um`, `untergrenze_um` und die reale Vollzeit `voll_real_um`; `halten_aktiv` markiert, ab wann die Untergrenze real hält |
| `entscheidung` | nach jedem Lauf, der ein Register, den Modus oder einen Timer geändert hat | dasselbe, mit den Werten genau dieses Laufs |
| `slot` | jede halbe Stunde aus dem Profil-Lauf | Hausverbrauch der Halbstunde (ohne Wallbox), Wallbox-Ladung, Energie am Notstromausgang, Halte-Lage der Entlade-Untergrenze, Register |
| `wetter` | jede halbe Stunde, nur mit Wetter-Entitäten | je Integration die Temperaturprognose der nächsten 24 Stunden neben dem gemessenen Außenfühler |
| `optimizer` | stündlich, nur mit Optimizer-Adresse | Fahrplan des evcc-Optimizers (Ladebeginn, Vollzeit, Nacht-Minimum, Ladestand-Verlauf) neben den Werten des Blueprints für denselben Lauf |

Die `kennung` jeder Zeile ist die Lauf-Kennung aus dem Logbuch: Zur Meldung `[V6.6.0 · 08:30:02]`
gehört die Zeile mit `"kennung": "08:30:02"` und `"art": "entscheidung"`. Ohne Aufzeichnung
erinnert der Blueprint einmal täglich bei Sonnenuntergang daran, dass die Entscheidungen des Tages
nicht nachgerechnet werden können.

Die Datei wächst um etwa 1 MB je Tag und beschreibt den Standort im Detail. Sie gehört deshalb
**nicht** nach `/config/www/`: Alles darunter liefert Home Assistant unter `/local/` ohne Anmeldung
aus. Abholen per Samba, Datei-Editor oder `scp`. Lokal unter `tests/fixtures/` abgelegt (dort per
`.gitignore` vom Repo ausgeschlossen) bauen die Tests jeden aufgezeichneten Lauf nach und rechnen
ihn durch dieselbe Variablenkette; ohne Aufzeichnung überspringen sie diese Prüfungen.

## Entwicklung

`pip install -r requirements-dev.txt && pytest -q` rendert die Variablenkette des Blueprints
außerhalb von Home Assistant gegen synthetische Szenarien und gegen die aufgezeichneten Läufe.
Dazu kommt die Prioritätskaskade selbst: je Zweig ein Szenario, das festhält, welcher Zweig
gewinnt und was er an den Wechselrichter schreibt. Ein Test liest die Zweige aus dem Blueprint und
vergleicht sie mit den erreichten, damit ein neuer Zweig nicht ungeprüft bleibt.
Arbeitsregeln, Prüfliste und Log-Konvention stehen in `CLAUDE.md`. Lizenz: Apache 2.0.
