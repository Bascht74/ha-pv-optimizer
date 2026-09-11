"""
Diagnose-Aufzeichnung: Der Blueprint schreibt jede halbe Stunde eine JSON-Zeile mit
allen Eingangswerten. Die Tests belegen, dass die Zeile vollstaendig ist und dass
sich aus ihr derselbe Lauf wieder aufbauen laesst - sonst taugt sie nicht als Fixture.
"""
from __future__ import annotations

import json

import pytest

from conftest import (AUFZEICHNUNG_ENTITY, FIXTURES_PFAD, aufzeichnung_lesen, aufzeichnungen, standard_inputs,
                      szenario, szenario_aus_aufzeichnung, zeit)
from ha_jinja import Harness, Zustand, input_definitionen


def _block(blueprint: dict) -> dict:
    for s in blueprint["action"]:
        if isinstance(s, dict) and "if" in s and AUFZEICHNUNG_ENTITY in str(s.get("if")):
            return s
    raise AssertionError("Aufzeichnungs-Block nicht gefunden")


def aufzeichnen(harness: Harness, blueprint: dict) -> dict:
    """Rendert die Meldung des Blocks so, wie der Blueprint sie schreiben wuerde."""
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


def test_aufzeichnung_erfasst_jeden_input(blueprint, tag):
    h = szenario(blueprint, zeit(tag, 10, 0))
    aufz = aufzeichnen(h, blueprint)
    definitionen = input_definitionen(blueprint)
    entity_inputs = {n for n, d in definitionen.items() if "entity" in (d.get("selector") or {})}
    konfig_inputs = set(definitionen) - entity_inputs

    # Jeder belegte Entity-Input steht drin, leere (Pack 3) nicht.
    belegt = {n for n in entity_inputs if h.inputs[n] != ""}
    assert {e["input"] for e in aufz["entitaeten"]} == belegt
    # Jeder Konfigurationswert steht drin und ist der unveraenderte Input - nicht ein
    # spaeter umgerechneter Wert derselben Variablen.
    assert set(aufz["konfiguration"]) == konfig_inputs
    for n in konfig_inputs:
        assert aufz["konfiguration"][n] == h.inputs[n], n
    # Die Attribute, die der Blueprint liest, sind dabei.
    je_input = {e["input"]: e for e in aufz["entitaeten"]}
    assert len(je_input["solcast_heute_sensor"]["attributes"]["detailedForecast"]) == 48
    assert "estimate10" in je_input["solcast_next_hour_sensor"]["attributes"]
    assert "raw_drift_soc" in je_input["schatten_bms_sensor"]["attributes"]
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


def _zeilen():
    for datei in sorted(FIXTURES_PFAD.glob("*.jsonl")):
        for nr, zeile in aufzeichnungen(datei):
            yield pytest.param(zeile, id=f"{datei.name}:{nr}")


@pytest.mark.parametrize("zeile", list(_zeilen()))
def test_echte_aufzeichnungen_laufen_durch_die_kette(blueprint, zeile):
    """Jede echte Zeile rendert die komplette Variablenkette ohne Fehler."""
    ctx = szenario_aus_aufzeichnung(blueprint, aufzeichnung_lesen(zeile)).auswerten()
    assert "target_p5" in ctx


# --------------------------------------------------------------------------
# Entlade-Untergrenze an einem echten Abend: Aufzeichnung plus die Tagesprognose
# der Folgetage aus deren erster Zeile (Solcast-Stand um Mitternacht).
# --------------------------------------------------------------------------
def _zeilen_von(datei):
    return [aufzeichnung_lesen(z) for _, z in aufzeichnungen(FIXTURES_PFAD / datei)]


def _eintrag(z, inp):
    return next(e for e in z["entitaeten"] if e["input"] == inp)


def _tagesprognose(z):
    fc = _eintrag(z, "solcast_heute_sensor")["attributes"]["detailedForecast"]
    return round(sum(s["pv_estimate"] for s in fc) * 0.5, 2), round(sum(s["pv_estimate10"] for s in fc) * 0.5, 2)


def test_entlade_untergrenze_an_einem_echten_septemberabend(blueprint):
    """
    Dachterrasse, 06.09. 21:00, Ladestand 95 %: Prognose fuer den 07.09. laut dessen
    Mitternachtszeile 40.94 kWh (P10 35.54), Tagesverbrauch aus dem Profil 11.8 kWh.
    Von Hand: Blend 38.24 x 0.92 - 11.8 = 23.4 kWh = 73 % von 32.15 kWh ->
    Ziel-Kandidat 90 - 73 = 17, Einspeise-Kandidat 27, Mindest 50 -> F = max(17, min(50, 27)) = 27 -> 25.
    Im September bindet die Untergrenze also nicht: Die Nacht fiel real nur auf 80 %.
    """
    from conftest import fake_entity
    zs = _zeilen_von("dachterrasse_2026-09-04_bis_11.jsonl")
    abend = next(z for z in zs if z["zeit"].startswith("2026-09-06T21:00"))
    morgen = next(z for z in zs if z["zeit"].startswith("2026-09-07T00:00"))
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
    assert ctx["b_stern_pct"] == pytest.approx(73, abs=1)
    assert ctx["f_roh"] == pytest.approx(27, abs=1)
    assert ctx["f_soc"] == 25
    nacht = [float(_eintrag(z, "battery_soc_sensor")["state"]) for z in zs
             if "2026-09-06T21:00" <= z["zeit"][:16] <= "2026-09-07T08:00"]
    assert min(nacht) == 80


# --------------------------------------------------------------------------
# Morgen-Blockade und Drosselung nur, wenn eine Einspeisespitze zu erwarten ist
# --------------------------------------------------------------------------
def _lauf(blueprint, datei, zeit_prefix):
    z = next(x for x in _zeilen_von(datei) if x["zeit"].startswith(zeit_prefix))
    return szenario_aus_aufzeichnung(blueprint, z).auswerten()


def test_ohne_erwartete_spitze_keine_blockade_aber_gedrosselt(blueprint):
    """
    PV, 05.09. 08:00: Prognose-Spitze 5,8 kW nach Abzug des Hausverbrauchs, Schwelle
    11,9 kW, 90 % davon 10,7 kW. Nichts zu kappen -> keine Blockade. Prio 7 bleibt
    gedrosselt (Simulation 18 A fuer 8,3 kWh in 11 h), damit die Batterie so spaet
    voll wird, wie die Prognose es zulaesst. Real hielt die Blockade an diesem Tag
    bis 13:00 bei 14 kWh Einspeisung, und die Batterie wurde nicht voll.
    """
    ctx = _lauf(blueprint, "pv_2026-09-04_bis_11.jsonl", "2026-09-05T08:00")
    assert ctx["spitze_erwartet_w"] == pytest.approx(5776, abs=5)
    assert ctx["spitze_erwartet"] is False
    assert ctx["blockade_aktiv"] is False
    assert ctx["sim_ampere_min"] == 18
    assert ctx["target_p5"] == 21


def test_mit_erwarteter_spitze_bleibt_alles_wie_bisher(blueprint):
    """Dachterrasse, 06.09. 08:00: Spitze 6,1 kW >= 90 % von 6,5 kW -> Blockade und Drosselung wie zuvor."""
    ctx = _lauf(blueprint, "dachterrasse_2026-09-04_bis_11.jsonl", "2026-09-06T08:00")
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
    ctx = _lauf(blueprint, "pv_2026-09-04_bis_11.jsonl", "2026-09-09T08:30")
    assert ctx["blockade_aktiv"] is False and ctx["spitze_erwartet"] is False
    assert ctx["benoetigt_kwh"] == pytest.approx(12.55, abs=0.05)
    assert ctx["brutto_ueberschuss_rest"] == pytest.approx(15.94, abs=0.05)
    assert ctx["puffer_fall_b"] == pytest.approx(1.0)
    assert ctx["fall_b_aktiv"] is True
    vorher = _lauf(blueprint, "pv_2026-09-04_bis_11.jsonl", "2026-09-09T08:00")
    assert vorher["fall_b_aktiv"] is False


@pytest.mark.parametrize("zeit_prefix, f_soc, erster_tag", [
    ("2026-09-11T03:00", 20, "heute"),    # heute 37.5 kWh Blend -> Nacht frei; mit "morgen" 25 kWh stuende hier 50
    ("2026-09-10T21:00", 25, "morgen"),   # abends zaehlt morgen
])
def test_horizont_der_entlade_planung_in_der_nacht(blueprint, zeit_prefix, f_soc, erster_tag):
    """PV: Die zweite Nachthaelfte plant mit der heutigen Prognose, der Abend mit der morgigen."""
    ctx = _lauf(blueprint, "pv_2026-09-04_bis_11.jsonl", zeit_prefix)
    assert ctx["entlade_aktiv"] is True
    assert ctx["prognose_tage"][0]["name"] == erster_tag
    assert ctx["f_soc"] == f_soc
