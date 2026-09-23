"""
Diagnose-Aufzeichnung: Der Blueprint schreibt jede halbe Stunde eine JSON-Zeile mit
allen Eingangswerten. Die Tests belegen, dass die Zeile vollstaendig ist und dass
sich aus ihr derselbe Lauf wieder aufbauen laesst - sonst taugt sie nicht als Fixture.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from conftest import (AUFZEICHNUNG_ENTITY, FIXTURES_PFAD, aufzeichnung_lesen, aufzeichnungen, standard_inputs,
                      szenario, szenario_aus_aufzeichnung, zeit, zerrissene_zeilen)
from ha_jinja import Harness, Zustand, input_definitionen


def _block(blueprint: dict) -> dict:
    for s in blueprint["action"]:
        if isinstance(s, dict) and "if" in s and AUFZEICHNUNG_ENTITY in str(s.get("if")):
            return s
    raise AssertionError("Aufzeichnungs-Block nicht gefunden")


def aufzeichnen(harness: Harness, blueprint: dict) -> dict:
    """Rendert die Meldung des Blocks so, wie der Blueprint sie schreiben wuerde."""
    harness.states.tabelle.setdefault(AUFZEICHNUNG_ENTITY, Zustand(AUFZEICHNUNG_ENTITY, "unknown"))
    ctx = harness.auswerten()
    nachricht = None
    for schritt in _block(blueprint)["then"]:
        if "variables" in schritt:
            ctx.update(harness._aufloesen(schritt["variables"], ctx))
        elif schritt.get("action") == "notify.send_message":
            nachricht = harness._aufloesen(schritt["data"]["message"], ctx)
    assert nachricht is not None, "kein notify.send_message im Block"
    return nachricht if isinstance(nachricht, dict) else json.loads(nachricht)


def test_block_feuert_nur_im_halbstundenlauf_und_nur_mit_entitaet(blueprint):
    block = _block(blueprint)
    bedingungen = str(block["if"])
    assert "monitoring_5min" in bedingungen
    assert "minute % 30 < 5" in bedingungen
    assert f"{AUFZEICHNUNG_ENTITY} is not none" in bedingungen.replace("states.", "")
    ziel = next(s for s in block["then"] if s.get("action") == "notify.send_message")["target"]["entity_id"]
    assert ziel == AUFZEICHNUNG_ENTITY


def test_aufzeichnung_haelt_beide_soc_skalen_fest(blueprint, tag):
    """
    Ohne die Werte beider BMS und den Versatz laesst sich eine Nacht nicht
    nachrechnen: Im Logbuch steht die Steuerungs-Skala, am Register die andere.
    """
    aufz = aufzeichnen(szenario(blueprint, zeit(tag, 21, 0), soc=50.0, schatten_soc=62.0), blueprint)
    r = aufz["rechnung"]
    assert r["soc_deye"] == 50.0 and r["soc_versatz"] == -12.0 and r["schatten_gueltig"] is True
    assert r["tou_ist_register"] == 20 and r["tou_ist"] == 32
    assert r["tou_schreibwert"] == r["f_soc"] - 12
    assert "schatten_soc_sensor" in {e["input"] for e in aufz["entitaeten"]}


def test_aufzeichnung_erfasst_jeden_input(blueprint, tag):
    h = szenario(blueprint, zeit(tag, 10, 0))
    aufz = aufzeichnen(h, blueprint)
    definitionen = input_definitionen(blueprint)
    entity_inputs = {n for n, d in definitionen.items() if "entity" in (d.get("selector") or {})}
    konfig_inputs = set(definitionen) - entity_inputs

    # Jeder belegte Entity-Input steht drin, leere (Pack 3) nicht.
    belegt = {n for n in entity_inputs if h.inputs[n] not in ("", [])}
    assert {e["input"] for e in aufz["entitaeten"]} == belegt
    # Mehrfachauswahl: ein Eintrag je gewaehlter Entitaet
    assert sum(1 for e in aufz["entitaeten"] if e["input"] == "wallbox_kwh_sensor") == len(h.inputs["wallbox_kwh_sensor"])
    # Jeder Konfigurationswert steht drin und ist der unveraenderte Input - nicht ein
    # spaeter umgerechneter Wert derselben Variablen.
    assert set(aufz["konfiguration"]) == konfig_inputs
    for n in konfig_inputs:
        assert aufz["konfiguration"][n] == h.inputs[n], n
    # Die Attribute, die der Blueprint liest, sind dabei.
    je_input = {e["input"]: e for e in aufz["entitaeten"]}
    assert len(je_input["solcast_heute_sensor"]["attributes"]["detailedForecast"]) == 48
    assert "estimate10" in je_input["solcast_next_hour_sensor"]["attributes"]
    assert "temperature" in je_input["wp_water_heater"]["attributes"]
    assert aufz["sun"] == "above_horizon"
    assert aufz["trigger"] == "monitoring_5min"


VERGLEICH = ["aktueller_soc", "grid_export", "ww_temp", "aktueller_ladestrom", "packs_online",
             "batterie_kapazitaet", "freie_kwh", "temperatur_limit_ampere", "pv_abschlag", "trend_faktor",
             "blockade_dyn_json", "blockade_dyn", "fall_b_aktiv", "sim_a_fenster", "target_p5",
             "wp_boost_karenz_ok", "wp_boost_startet_gleich", "ladefenster_reicht_nicht"]


@pytest.mark.parametrize("hh, soc", [(9, 60.0), (12, 84.0), (16, 97.0)])
def test_aufzeichnung_reproduziert_den_lauf(blueprint, tag, hh, soc):
    """Aus der Zeile gebaut, rechnet das Harness dieselben Werte wie der Original-Lauf."""
    original = szenario(blueprint, zeit(tag, hh, 0), soc=soc)
    aufz = aufzeichnen(original, blueprint)
    # Ueber JSON-Text, wie es aus der Datei kaeme - Praefix der File-Integration inklusive.
    zeile = "2026-09-04T10:00:00.123456 " + json.dumps(aufz)
    kopie = szenario_aus_aufzeichnung(blueprint, aufzeichnung_lesen(zeile))

    assert kopie.jetzt == original.jetzt
    assert kopie.inputs == original.inputs
    a, b = original.auswerten(), kopie.auswerten()
    fehlend = [k for k in VERGLEICH if k not in a]
    assert fehlend == [], f"Vergleichsvariablen nicht in der Kette: {fehlend}"
    for k in VERGLEICH:
        assert a[k] == b[k], f"{k}: Original {a[k]!r}, aus Aufzeichnung {b[k]!r}"


# Aufzeichnungen sind private Standortdaten und liegen nur lokal (tests/fixtures/*.jsonl ist
# per .gitignore ausgeschlossen). Ohne Datei ueberspringen sich die Tests darauf.
def _zeilen():
    params = [pytest.param(zeile, id=f"{datei.name}:{nr}")
              for datei in sorted(FIXTURES_PFAD.glob("*.jsonl")) for nr, zeile in aufzeichnungen(datei)]
    return params or [pytest.param(None, id="keine", marks=pytest.mark.skip(reason="keine lokale Aufzeichnung in tests/fixtures"))]


@pytest.mark.parametrize("zeile", _zeilen())
def test_echte_aufzeichnungen_laufen_durch_die_kette(blueprint, zeile):
    """Jede echte Zeile rendert die komplette Variablenkette ohne Fehler."""
    ctx = szenario_aus_aufzeichnung(blueprint, aufzeichnung_lesen(zeile)).auswerten()
    assert "target_p5" in ctx


def test_zerrissene_zeilen_werden_uebersprungen_und_gemeldet(blueprint, tag, tmp_path):
    """
    Schreiben zwei Laeufe gleichzeitig in die Datei, steht die zweite Aufzeichnung mitten in
    der ersten. Der Loader laesst so eine Zeile weg und nennt ihre Nummer, statt an ihr zu
    scheitern; die Zeilen davor und danach bleiben lesbar.
    """
    a = json.dumps(aufzeichnen(szenario(blueprint, zeit(tag, 9, 0), soc=60.0), blueprint))
    b = json.dumps(aufzeichnen(szenario(blueprint, zeit(tag, 9, 30), soc=62.0), blueprint))
    schnitt = a.index('"zeit": "') + len('"zeit": "')
    zerrissen = a[:schnitt] + b + a[schnitt:]
    datei = tmp_path / "pv_2026-09-04.jsonl"
    datei.write_text("Home Assistant notifications (Log started: 2026-09-04T00:00:00+00:00)\n" + "-" * 80 + "\n"
                     + "test\n" + "2026-09-04T09:00:00.123456 " + a + "\n" + zerrissen + "\n" + b + "\n",
                     encoding="utf-8")
    with pytest.warns(UserWarning, match=r"pv_2026-09-04\.jsonl: 1 zerrissene Zeile\(n\) uebersprungen: \[5\]"):
        gelesen = aufzeichnungen(datei)
    assert [nr for nr, _ in gelesen] == [4, 6]
    assert [aufzeichnung_lesen(z)["zeit"] for _, z in gelesen] == [json.loads(a)["zeit"], json.loads(b)["zeit"]]
    assert zerrissene_zeilen(datei) == [5]


# --------------------------------------------------------------------------
# Entlade-Untergrenze an einem echten Abend: Aufzeichnung plus die Tagesprognose
# der Folgetage aus deren erster Zeile (Solcast-Stand um Mitternacht).
# --------------------------------------------------------------------------
def _zeilen_von(standort):
    """
    Alle Laeufe der lokalen Aufzeichnung(en) eines Standorts (`<standort>_<datum>.jsonl`; das
    Datum im Muster haelt einen Download unter seinem Originalnamen pv_optimizer_aufzeichnung.jsonl
    fern). Ohne Datei oder ohne lesbaren Lauf wird der Test uebersprungen.
    """
    dateien = sorted(FIXTURES_PFAD.glob(f"{standort}_20*.jsonl"))
    zs = [aufzeichnung_lesen(z) for datei in dateien for _, z in aufzeichnungen(datei)]
    if not zs:
        pytest.skip(f"keine lokale Aufzeichnung mit lesbaren Laeufen: {standort}_<datum>.jsonl")
    return zs


def _zeile_um(zs, zeit_prefix):
    """Der Lauf zu einem Zeitpunkt; fehlt er in der lokalen Aufzeichnung, wird der Test uebersprungen."""
    z = next((x for x in zs if x["zeit"].startswith(zeit_prefix)), None)
    if z is None:
        pytest.skip(f"kein Lauf {zeit_prefix} in der lokalen Aufzeichnung")
    return z


def _eintrag(z, inp):
    return next(e for e in z["entitaeten"] if e["input"] == inp)


def _tagesprognose(z):
    fc = _eintrag(z, "solcast_heute_sensor")["attributes"]["detailedForecast"]
    return round(sum(s["pv_estimate"] for s in fc) * 0.5, 2), round(sum(s["pv_estimate10"] for s in fc) * 0.5, 2)


def test_entlade_untergrenze_an_einem_echten_septemberabend(blueprint):
    """
    Dachterrasse, 06.09. 21:00, Ladestand 95 %: Prognose fuer den 07.09. laut dessen
    Mitternachtszeile 40.94 kWh (P10 35.54), Tagesverbrauch aus dem Profil 11.8 kWh.
    Von Hand: Blend 38.24 kWh in heutiger Form, Ueberschuss ab 07:30 bis zum Abend
    38.24 x 0.92 - Tagesverbrauch der PV-Stunden (7.0 kWh) = 28.2 kWh Zwischenmaximum = 87.7 % ->
    Ziel-Kandidat 90 - 87.7 = 2.3, Einspeise-Kandidat 12.3, Mindest 50 -> F = max(2.3, min(50, 12.3)) = 12.3 -> Minimum 20.
    Im September bindet die Untergrenze also nicht: Die Nacht fiel real nur auf 80 %.
    """
    from conftest import fake_entity
    zs = _zeilen_von("dachterrasse")
    abend = _zeile_um(zs, "2026-09-06T21:00")
    morgen = _zeile_um(zs, "2026-09-07T00:00")
    p50, p10 = _tagesprognose(morgen)
    assert (p50, p10) == (40.94, 35.54)

    h = szenario_aus_aufzeichnung(blueprint, abend)
    eid = fake_entity("solcast_morgen_sensor", "sensor")
    h.inputs["solcast_morgen_sensor"] = eid
    h.states.tabelle[eid] = Zustand(eid, str(p50), {"estimate10": p10})
    ctx = h.auswerten(bis="tou_schreiben")

    assert ctx["entlade_aktiv"] is True
    assert ctx["aktueller_soc"] == 95
    assert ctx["verbrauch_tag_kwh"] == pytest.approx(11.8, abs=0.05)
    assert ctx["plan_start"].endswith("2026-09-07T07:30:00+02:00")
    assert ctx["b_stern_pct"] == pytest.approx(87.7, abs=0.5)
    assert ctx["f_roh"] == pytest.approx(12.3, abs=0.5)
    assert ctx["f_soc"] == 20
    nacht = [float(_eintrag(z, "battery_soc_sensor")["state"]) for z in zs
             if "2026-09-06T21:00" <= z["zeit"][:16] <= "2026-09-07T08:00"]
    assert min(nacht) == 80


def test_notstromreserve_an_einem_echten_septemberabend(blueprint):
    """
    Derselbe Abend, das ganze Haus als Notstromlast (Hausprofil in Wh): 21 Halbstunden von 21:00 bis
    07:30 mit 3670 Wh Profil + 21 x 45 Wh Eigenverbrauch = 4615 Wh / 0,95 = 4,858 kWh, minus P10-PV
    der Slots 06:00-07:30 (0,138 kWh x 0,92 = 0,127) = 4,73 kWh bis 07:30, danach liegt PV10 ueber der Last.
    Abschalt-Ladestand 5 %: 5 + 4,73 / 32,15 = 19,7 % -> 20 %, gleich dem Minimum; mit 9 %: 23,7 % -> 25 %.
    """
    from conftest import fake_entity, reserve_referenz, tage_aus_szenario
    zs = _zeilen_von("dachterrasse")
    abend = _zeile_um(zs, "2026-09-06T21:00")
    morgen = _zeile_um(zs, "2026-09-07T00:00")
    p50, p10 = _tagesprognose(morgen)
    wh = [round(float(v) * 1000) for v in json.loads(_eintrag(abend, "hausverbrauch_json_text")["state"])]
    assert sum(wh[42:] + wh[:15]) == 3670

    def lauf(shutdown):
        h = szenario_aus_aufzeichnung(blueprint, abend)
        for name, state, attrs in (("solcast_morgen_sensor", str(p50), {"estimate10": p10}),
                                   ("notstrom_json_text", json.dumps(wh), {}), ("wr_shutdown_soc", str(shutdown), {})):
            eid = fake_entity(name, "sensor" if name.endswith("sensor") else ("number" if name.startswith("wr_") else "input_text"))
            h.inputs[name] = eid
            h.states.tabelle[eid] = Zustand(eid, state, attrs)
        return h.auswerten(bis="tou_schreiben")

    ctx = lauf(5)
    assert ctx["reserve_plan"]["kwh"] == pytest.approx(4.73, abs=0.01)
    assert ctx["reserve_plan"]["bis"].endswith("2026-09-07T07:30:00+02:00")
    assert ctx["reserve_pct"] == pytest.approx(19.7, abs=0.05) and ctx["reserve_5"] == 20 and ctx["f_soc"] == 20
    jetzt = dt.datetime.fromisoformat(abend["zeit"])
    fc = _eintrag(abend, "solcast_heute_sensor")["attributes"]["detailedForecast"]
    ref = reserve_referenz(jetzt, tage_aus_szenario(jetzt, (p50,), forecast=fc, p10_anteil=p10 / p50, a=0.0),
                           wh, kap_kwh=ctx["batterie_kapazitaet"], shutdown=5)
    assert ctx["reserve_plan"]["kwh"] == pytest.approx(ref["kwh"], abs=0.001) and ctx["reserve_plan"]["bis"] == ref["bis"].isoformat()
    neun = lauf(9)
    assert neun["reserve_5"] == 25 and neun["f_roh"] == 25 and neun["f_soc"] == 25 and neun["tou_schreiben"] is False


# --------------------------------------------------------------------------
# Morgen-Blockade und Drosselung nur, wenn eine Einspeisespitze zu erwarten ist
# --------------------------------------------------------------------------
def _lauf(blueprint, standort, zeit_prefix):
    return szenario_aus_aufzeichnung(blueprint, _zeile_um(_zeilen_von(standort), zeit_prefix)).auswerten()


def test_ohne_erwartete_spitze_keine_blockade_aber_gedrosselt(blueprint):
    """
    PV, 05.09. 08:00: Prognose-Spitze 5,8 kW nach Abzug des Hausverbrauchs, Schwelle
    11,9 kW, 90 % davon 10,7 kW. Nichts zu kappen -> keine Blockade. Prio 7 bleibt
    gedrosselt (Simulation 18 A fuer 8,3 kWh in 11 h), damit die Batterie so spaet
    voll wird, wie die Prognose es zulaesst. Real hielt die Blockade an diesem Tag
    bis 13:00 bei 14 kWh Einspeisung, und die Batterie wurde nicht voll.
    """
    ctx = _lauf(blueprint, "pv", "2026-09-05T08:00")
    assert ctx["spitze_erwartet_w"] == pytest.approx(5776, abs=5)
    assert ctx["spitze_erwartet"] is False
    assert ctx["blockade_aktiv"] is False
    assert ctx["sim_ampere_min"] == 18
    assert ctx["target_p5"] == 21


def test_mit_erwarteter_spitze_bleibt_alles_wie_bisher(blueprint):
    """Dachterrasse, 06.09. 08:00: Spitze 6,1 kW >= 90 % von 6,5 kW -> Blockade und Drosselung wie zuvor."""
    ctx = _lauf(blueprint, "dachterrasse", "2026-09-06T08:00")
    assert ctx["spitze_erwartet_w"] == pytest.approx(6130, abs=5)
    assert ctx["spitze_erwartet"] is True
    assert ctx["blockade_aktiv"] is True
    assert ctx["target_p5"] < 100


# --------------------------------------------------------------------------
# Fall B an einem knappen Morgen: der Faktor entscheidet, und er lag richtig
# --------------------------------------------------------------------------
def test_fall_b_am_knappen_morgen(blueprint):
    """
    PV, 09.09. 08:30, Ladestand 39 %: Bedarf 12.6 kWh x 1.2 = 15.1 kWh gegen
    Brutto-Ueberschuss 15.9 kWh - Puffer 1.0 kWh = 14.9 kWh -> Fall B. Der Tag
    lieferte real 20.5 kWh statt 31.5 kWh P50; ohne Fall B waere die Batterie
    bei rund 92 % stehen geblieben. Eine halbe Stunde vorher reichte es noch.
    """
    ctx = _lauf(blueprint, "pv", "2026-09-09T08:30")
    assert ctx["blockade_aktiv"] is False and ctx["spitze_erwartet"] is False
    assert ctx["benoetigt_kwh"] == pytest.approx(12.55, abs=0.05)
    assert ctx["brutto_ueberschuss_rest"] == pytest.approx(15.94, abs=0.05)
    assert ctx["puffer_fall_b"] == pytest.approx(1.0)
    assert ctx["fall_b_aktiv"] is True
    vorher = _lauf(blueprint, "pv", "2026-09-09T08:00")
    assert vorher["fall_b_aktiv"] is False


@pytest.mark.parametrize("zeit_prefix, f_soc, erster_tag", [
    ("2026-09-11T03:00", 20, "heute"),    # heute 37.5 kWh Blend ab 08:00 -> Nacht frei
    ("2026-09-10T21:00", 20, "morgen"),   # abends derselbe Tag als morgen, gleiche Freigabe
])
def test_horizont_der_entlade_planung_in_der_nacht(blueprint, zeit_prefix, f_soc, erster_tag):
    """PV: Vor und nach Mitternacht plant dieselbe Sonnenstrecke, nur der Name des ersten Tags wechselt."""
    ctx = _lauf(blueprint, "pv", zeit_prefix)
    assert ctx["entlade_aktiv"] is True
    assert ctx["prognose_tage"][0]["name"] == erster_tag
    assert ctx["plan_start"].endswith("2026-09-11T08:00:00+02:00")
    assert ctx["f_soc"] == f_soc


def test_aufzeichnung_traegt_die_rechenwerte(blueprint, tag):
    """Die Zeile enthaelt die Entscheidungsgroessen des Laufs, nicht nur die Eingangswerte."""
    h = szenario(blueprint, zeit(tag, 10, 0))
    ctx = h.auswerten()
    aufz = aufzeichnen(h, blueprint)
    r = aufz["rechnung"]
    for k in ("f_soc", "tou_ist", "tou_schreiben", "halten_aktiv", "halten_verlust_kwh", "prognose_tage",
              "target_p5", "fall_b_aktiv", "blockade_aktiv", "spitze_erwartet", "trend_faktor", "benoetigt_kwh",
              "plan_voll_um", "fallb_voll_um", "untergrenze_um", "haus_live_kwh", "auffuellung_heute_pct"):
        assert k in r, k
    assert r["f_soc"] == ctx["f_soc"] and r["target_p5"] == ctx["target_p5"]


@pytest.mark.parametrize("standort", ["pv", "dachterrasse"])
def test_aufzeichnung_rendert_fuer_echte_laeufe(blueprint, standort):
    """to_json darf an keinem Rechenwert scheitern (datetime, Undefined): jede 12. Zeile beider Standorte."""
    zs = _zeilen_von(standort)
    for z in zs[::12]:
        aufz = aufzeichnen(szenario_aus_aufzeichnung(blueprint, z), blueprint)
        assert "rechnung" in aufz and "f_soc" in aufz["rechnung"], z["zeit"]


def test_zeilenarten_lauf_und_entscheidung(blueprint, tag):
    """Die Halbstundenzeile traegt art=lauf; die Entscheidungszeile am Laufende dieselben Daten mit art=entscheidung."""
    h = szenario(blueprint, zeit(tag, 10, 0))
    h.states.tabelle[AUFZEICHNUNG_ENTITY] = Zustand(AUFZEICHNUNG_ENTITY, "unknown")
    ctx = h.auswerten()
    tick = aufzeichnen(h, blueprint)
    assert tick["art"] == "lauf" and "rechnung" in tick and "entitaeten" in tick
    ende = next(s for s in reversed(blueprint["action"]) if isinstance(s, dict) and "if" in s and "entscheidung_im_lauf" in str(s["if"]))
    nachricht = h._aufloesen(ende["then"][0]["data"]["message"], ctx)
    zeile = nachricht if isinstance(nachricht, dict) else json.loads(nachricht)
    assert zeile["art"] == "entscheidung" and zeile["rechnung"] == tick["rechnung"]
    assert tick["kennung"] == zeile["kennung"] == ctx["lauf_kennung"]
    assert ctx["log_kopf"] == f"{ctx['bp_version'][:-1]} · {ctx['lauf_kennung']}]"
