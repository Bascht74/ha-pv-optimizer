# Aufzeichnungen echter Läufe

Hier liegen die Dateien der Diagnose-Aufzeichnung des Blueprints: eine JSON-Zeile je
Halbstundenlauf mit allen Eingangswerten, so wie der Lauf sie gesehen hat. Einrichtung
steht im Kopf von `pv-steuerung.yaml_`, Download unter
`/local/pv_optimizer_aufzeichnung.jsonl` der Instanz.

Ablage: `<standort>_<datum>.jsonl`, z. B. `standort-a_2026-09-04.jsonl`. Ein
Zeitstempel-Präfix der File-Integration vor dem `{` ist erlaubt.

`test_aufzeichnung.py` schickt jede Zeile jeder Datei durch die Variablenkette. Das
belegt zunächst nur, dass die Kette mit echten Werten durchläuft. Eine Regression
entsteht daraus, wenn zu einer Zeile die geloggte Entscheidung des Tages als
Erwartungswert in einen Test wandert.
