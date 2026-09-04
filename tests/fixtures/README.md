# Aufzeichnungen echter Läufe

Hier liegen die Dateien der Diagnose-Aufzeichnung des Blueprints: eine JSON-Zeile je
Halbstundenlauf mit allen Eingangswerten, so wie der Lauf sie gesehen hat. Einrichtung
steht im Kopf von `pv-steuerung.yaml_`, Download unter
`/local/pv_optimizer_aufzeichnung.jsonl` der Instanz.

Ablage: `<standort>_<datum>.jsonl`. Die Dateien bleiben, wie sie heruntergeladen
wurden: Die zwei Kopfzeilen der File-Integration, Testsendungen und ein Zeitstempel-
Präfix vor dem `{` werden beim Einlesen übersprungen.

`test_aufzeichnung.py` schickt jede Zeile jeder Datei durch die Variablenkette. Das
belegt zunächst nur, dass die Kette mit echten Werten durchläuft. Eine Regression
entsteht daraus, wenn zu einer Zeile die geloggte Entscheidung des Tages als
Erwartungswert in einen Test wandert.
