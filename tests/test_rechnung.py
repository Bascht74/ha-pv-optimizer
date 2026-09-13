"""
Rechnungstests - die variables:-Kette wird mit dem Harness ausgewertet und
gegen von Hand berechnete Erwartungswerte geprueft.

Jeder Test hier ist einer der Defekte, die aus Logbuch-Analysen kamen, oder
eine Invariante, deren Verletzung einen solchen Defekt bedeuten wuerde.
Bei einem roten Test steht im Namen, welche Regel gerissen ist.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from conftest import plan_referenz, reserve_referenz, tage_aus_szenario, prognose_gleichmaessig, szenario, zeit
from ha_jinja import Zustand

# Gleichmaessige Prognose: 2,0 kW P50, 1,4 kW P10 -> Blend je Slot
# (2,0 x 0,5 + 1,4 x 0,5) x 0,5 h = 0,85 kWh.  Bei 20 Slots ab 08:00 endet
# das Ladefenster 17:30; mit 1 h Ladevorlauf sind die letzten 2 Slots weg.
KW = 2.0
BLEND = (KW * 0.5 + KW * 0.7 * 0.5) * 0.5
ABSCHLAG_MORGENS = 1 / 1.2


def test_kette_rendert_vollstaendig(blueprint, tag):
    ctx = szenario(blueprint, zeit(tag, 10, 5)).auswerten()
    assert isinstance(ctx["blockade_dyn"], dict) and "t" in ctx["blockade_dyn"]
    assert isinstance(ctx["target_p5"], (int, float))
    assert isinstance(ctx["fall_b_aktiv"], bool)
    assert isinstance(ctx["json_profil"], list) and len(ctx["json_profil"]) == 48


# --------------------------------------------------------------------------
# Morgen-Blockade: der Abschlag gehoert auf die PV, nicht auf den Ueberschuss
# --------------------------------------------------------------------------
def test_blockade_abschlag_auf_prognose_nicht_auf_ueberschuss(blueprint, tag):
    """
    (blend x f) - haus  statt  (blend - haus) x f.  Der Unterschied ist
    haus x (1 - f) je Slot - bei f = 1/1,2 also 16,7 % des Hausverbrauchs,
    die die alte Rechnung zu viel als verfuegbar auswies.
    """
    haus = 0.2
    h = szenario(blueprint, zeit(tag, 6, 30), soc=95.0,
                 forecast=prognose_gleichmaessig(tag, KW), profil_kwh=haus)
    d = h.auswerten(bis="blockade_dyn")["blockade_dyn"]

    # Ueberschuss gross, Bedarf klein -> T rastet an der Obergrenze 13:30 ein
    assert d["t"].startswith(f"{tag.isoformat()} 13:30"), d
    # ab 13:30 bis Fensterende 16:30 (17:00/17:30 = Ladevorlauf) sind es 7 Slots
    n = 7
    richtig = n * (BLEND * ABSCHLAG_MORGENS - haus)
    falsch = n * (BLEND - haus) * ABSCHLAG_MORGENS
    assert d["u_ab_t"] == pytest.approx(richtig, abs=0.02), \
        f"u_ab_t={d['u_ab_t']}, erwartet {richtig:.3f} (alte Rechnung ergaebe {falsch:.3f})"


def test_blockade_ohne_hausverbrauch_ist_reiner_abschlag(blueprint, tag):
    """Gegenprobe: bei haus = 0 sind beide Rechenwege gleich."""
    h = szenario(blueprint, zeit(tag, 6, 30), soc=95.0,
                 forecast=prognose_gleichmaessig(tag, KW), profil_kwh=0.0)
    d = h.auswerten(bis="blockade_dyn")["blockade_dyn"]
    assert d["u_ab_t"] == pytest.approx(7 * BLEND * ABSCHLAG_MORGENS, abs=0.02)


# --------------------------------------------------------------------------
# Realitaets-Check: der laufende Slot zaehlt anteilig auf die Ist-Seite
# --------------------------------------------------------------------------
def test_realitaets_check_zaehlt_laufenden_slot_anteilig(blueprint, tag):
    """
    20 min in den 10:00-Slot: vier volle Slots (08:00-09:30) plus 20/30 des
    laufenden. Ohne den Anteil ist der Nenner zu klein und der Faktor innerhalb
    jeder Halbstunde zu optimistisch - in der Richtung, die Ladung kostet.
    """
    h = szenario(blueprint, zeit(tag, 10, 20), forecast=prognose_gleichmaessig(tag, KW))
    t = h.auswerten(bis="trend_dict")["trend_dict"]
    erwartet = 4 * BLEND + BLEND * (20 / 30)
    assert t["blend_bisher"] == pytest.approx(erwartet, abs=0.005), \
        f"blend_bisher={t['blend_bisher']}, erwartet {erwartet:.3f} (ohne Anteil: {4 * BLEND:.3f})"


def test_realitaets_check_faktor_ist_ueber_die_halbstunde_konstant(blueprint, tag):
    """Anlage konstant bei 85 % der Prognose -> der Faktor saegt nicht mehr."""
    faktoren = []
    for minute in (0, 10, 20, 29):
        jetzt = zeit(tag, 14, minute)
        h = szenario(blueprint, jetzt, forecast=prognose_gleichmaessig(tag, KW))
        # Ist-Erzeugung = 85 % des bis jetzt prognostizierten Blends
        t = h.auswerten(bis="trend_dict")["trend_dict"]
        from ha_jinja import Zustand, Zustand
        h.states.tabelle[h.inputs["pv_erzeugung_heute_sensor"]] = Zustand(
            h.inputs["pv_erzeugung_heute_sensor"], str(round(0.85 * t["blend_bisher"], 4)))
        faktoren.append(h.auswerten(bis="trend_faktor")["trend_faktor"])
    assert max(faktoren) - min(faktoren) < 0.005, f"Faktor saegt: {faktoren}"
    assert faktoren[0] == pytest.approx(0.85, abs=0.005)


# --------------------------------------------------------------------------
# Waermepumpen-Boost: Karenz nach einem Ende
# --------------------------------------------------------------------------
@pytest.mark.parametrize("idle_seit_s, erwartet", [
    (2, False),        # zwei Sekunden nach dem Watchdog-Ende: das war die Oszillation
    (1799, False),     # knapp unter der Anlaufdauer (00:30:00)
    (1801, True),      # Anlaufdauer vorbei
    (6 * 3600, True),  # regulaerer Abstand zwischen zwei Boosts
])
def test_boost_karenz(blueprint, tag, idle_seit_s, erwartet):
    h = szenario(blueprint, zeit(tag, 13, 33), boost_timer_idle_seit_s=idle_seit_s)
    assert h.auswerten(bis="wp_boost_karenz_ok")["wp_boost_karenz_ok"] is erwartet


# --------------------------------------------------------------------------
# Ladestrom-Simulation
# --------------------------------------------------------------------------
def test_sim_ampere_steigt_mit_dem_bedarf_oder_meldet_unerreichbar(blueprint, tag):
    """
    Mehr Bedarf darf nie weniger Strom ergeben. Die Suche liefert 0 als
    Sentinel fuer "kein Strom bis max_ampere deckt den Bedarf im Fenster" -
    dann muss ladefenster_reicht_nicht greifen und Fall B uebernehmen.
    """
    erreichbar, unerreichbar = [], []
    for soc in (90, 80, 70, 60):
        ctx = szenario(blueprint, zeit(tag, 10, 5), soc=soc).auswerten(bis="ladefenster_reicht_nicht")
        a = ctx["sim_a_fenster"]
        if a > 0:
            erreichbar.append(a)
        else:
            unerreichbar.append(soc)
            assert ctx["ladefenster_reicht_nicht"] is True, \
                f"SOC {soc}: Simulation liefert 0, aber ladefenster_reicht_nicht ist False"
    assert erreichbar == sorted(erreichbar), f"nicht monoton: {erreichbar}"
    assert len(erreichbar) >= 2, "Fixture zu schwach: fast alle Faelle unerreichbar"
    # Ist ein Bedarf unerreichbar, sind alle groesseren es auch (SOC absteigend = Bedarf aufsteigend)
    assert unerreichbar == sorted(unerreichbar, reverse=True), unerreichbar


def test_json_profil_fallback_bei_leerem_helfer(blueprint, tag):
    from ha_jinja import Zustand
    h = szenario(blueprint, zeit(tag, 10, 5))
    h.states.tabelle[h.inputs["hausverbrauch_json_text"]] = Zustand(h.inputs["hausverbrauch_json_text"], "")
    assert h.auswerten(bis="json_profil")["json_profil"] == [0.2] * 48


# --------------------------------------------------------------------------
# Hausverbrauchs-Hinweis in Prio 7 und Fall B
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Fall B
# --------------------------------------------------------------------------
def test_fall_b_bei_knappem_rest_aktiv(blueprint, tag):
    """Nachmittag, 3,5 kWh Platz, wenig PV-Rest -> Eskalation."""
    h = szenario(blueprint, zeit(tag, 15, 30), soc=89.0)
    assert h.auswerten(bis="fall_b_aktiv")["fall_b_aktiv"] is True


def test_fall_b_bei_grossem_ueberschuss_inaktiv(blueprint, tag):
    """Frueher Morgen, wenig Platz, viel PV voraus -> keine Eskalation."""
    h = szenario(blueprint, zeit(tag, 8, 5), soc=93.0, forecast=prognose_gleichmaessig(tag, 4.0))
    assert h.auswerten(bis="fall_b_aktiv")["fall_b_aktiv"] is False


# --------------------------------------------------------------------------
# Kapazitaet haengt an den konfigurierten Packs, nicht an den meldenden
# --------------------------------------------------------------------------
def test_kapazitaet_bleibt_bei_bms_aussetzer(blueprint, tag):
    """
    Pack 2 meldet seit 15 s nicht. Der Temperaturschutz darf das sehen
    (packs_online = 1), das Energiemodell nicht: die Batterie hat weiterhin
    zwei Packs, und freie_kwh geht in Blockade, Fall B und Prio 7 ein.
    """
    from ha_jinja import Zustand
    h = szenario(blueprint, zeit(tag, 10, 5), soc=84.0)
    e = h.inputs["bms2_temp_min_sensor"]
    h.states.tabelle[e] = Zustand(e, "unavailable", last_changed=h.jetzt - dt.timedelta(seconds=15))
    ctx = h.auswerten(bis="freie_kwh")
    kapazitaet_2_packs = 2 * 314 * 51.2 / 1000
    assert ctx["packs_online"] == 1
    assert ctx["batterie_kapazitaet"] == pytest.approx(kapazitaet_2_packs, abs=0.05), \
        f"Kapazitaet {ctx['batterie_kapazitaet']} - halbiert durch einen Sensor-Aussetzer"
    assert ctx["freie_kwh"] == pytest.approx(kapazitaet_2_packs * 0.16, abs=0.05)


# --------------------------------------------------------------------------
# Boost-Start nur mit gueltigem Warmwasser-Sensor
# --------------------------------------------------------------------------
@pytest.mark.parametrize("ww_state, erwartet", [
    ("48.0", True),           # kalt, Export hoch, Timer idle -> Boost darf
    ("unavailable", False),   # Fallback 50 Grad laese sich als kalt - darf nicht
    ("unknown", False),
])
def test_boost_startet_nur_mit_gueltigem_warmwasser_sensor(blueprint, tag, ww_state, erwartet):
    from ha_jinja import Zustand
    h = szenario(blueprint, zeit(tag, 11, 5))
    g = h.inputs["grid_export_sensor"]
    h.states.tabelle[g] = Zustand(g, "-7000")            # 7 kW Einspeisung > Boost-Schwelle
    w = h.inputs["wp_temp_sensor"]
    h.states.tabelle[w] = Zustand(w, ww_state)
    assert h.auswerten(bis="wp_boost_startet_gleich")["wp_boost_startet_gleich"] is erwartet


# --------------------------------------------------------------------------
# Slot-Index des Verbrauchsprofils folgt dem Trigger, nicht der Uhr
# --------------------------------------------------------------------------
def _update_json_variablen(blueprint):
    schritt = next(s for s in blueprint["action"]
                   if isinstance(s, dict) and "if" in s and "update_json" in str(s["if"]))
    return {k: v for t in schritt["then"] if isinstance(t, dict) and "variables" in t for k, v in t["variables"].items()}


@pytest.mark.parametrize("trigger_hh, trigger_mm, verzug_min, erwartet", [
    (14, 0, 0, 27),    # puenktlich: Slot 13:30-14:00
    (14, 0, 17, 27),   # 17 Minuten in der Warteschlange: derselbe Slot, nicht 14:00-14:30
    (0, 0, 16, 47),    # Mitternachtslauf verspaetet: letzter Slot des Vortags
])
def test_slot_index_folgt_dem_trigger_nicht_der_uhr(blueprint, tag, trigger_hh, trigger_mm, verzug_min, erwartet):
    """
    mode: queued kann den Halbstundenlauf hinter andere Laeufe schieben. Der Slot,
    dessen Verbrauch verbucht wird, ist der vor der Trigger-Zeit - egal, wann der
    Lauf tatsaechlich rechnet. Sonst landet der Wert ab 15 Minuten Verzug im
    falschen Slot des EMA-Profils, an dem Blockade und Prio 7 haengen.
    """
    trigger_zeit = zeit(tag, trigger_hh, trigger_mm)
    h = szenario(blueprint, trigger_zeit + dt.timedelta(minutes=verzug_min), trigger_id="update_json")
    vars_ = _update_json_variablen(blueprint)
    ctx = {"trigger": {"id": "update_json", "now": trigger_zeit}}
    ctx.update(h._aufloesen({"var_hausverbrauch_json": vars_["var_hausverbrauch_json"]}, ctx))
    assert h._aufloesen(vars_["h_index"], ctx) == erwartet
    ist_mitternacht = h._aufloesen(vars_["ist_mitternachtslauf"], ctx)
    assert ist_mitternacht is (trigger_hh == 0), f"ist_mitternachtslauf={ist_mitternacht!r}"


# --------------------------------------------------------------------------
# Waermepumpen-Felder: Pflicht nur bei eingeschaltetem Boost
# --------------------------------------------------------------------------
@pytest.mark.parametrize("boost, fehlend_erwartet", [
    (False, []),
    (True, ["Warmwasserspeicher (Entität)", "Stellgröße: Hysterese (K)", "Stellgröße: Einmal-Ladung (Button)"]),
])
def test_wp_felder_sind_nur_mit_boost_pflicht(blueprint, tag, boost, fehlend_erwartet):
    """Ein Standort ohne Waermepumpe laesst die Felder leer; mit Boost muessen sie belegt sein."""
    h = szenario(blueprint, zeit(tag, 10, 0), input_overrides={
        "wp_boost_aktiv": boost, "wp_water_heater": "", "wp_hysterese_number": "", "wp_boost_button": ""})
    fehlend = h.auswerten(bis="pflicht_liste")["pflicht_liste"]
    assert fehlend == fehlend_erwartet


# --------------------------------------------------------------------------
# Entlade-Untergrenze: nachts nur so tief, wie die naechsten Tage auffuellen
# --------------------------------------------------------------------------
# Erwartungswerte von Hand: Kapazitaet 32,15 kWh, Verbrauch 48 x 0,19 = 9,12 kWh/Tag,
# Wirkungsgrad 0,92, Vertrauen 1 / 0,7 / 0,5, P10 = P50, Mindest 50 %, Ziel 90 %.
def _tou(soc):
    from conftest import fake_entity
    return {fake_entity(f"wr_tou_{i}", "number"): Zustand(fake_entity(f"wr_tou_{i}", "number"), str(soc)) for i in range(1, 7)}


@pytest.mark.parametrize("lage, prognose, b_stern, f_soc", [
    ("Sommer: 40/40/40 kWh, alles wuerde einspeisen -> Minimum", (40, 40, 40), 192.7, 20),
    ("Herbst: 15/5/5 kWh, morgen 10,1 kWh Zwischenmaximum = 31,4 % -> 90 - 31,4 = 58,6 -> 55", (15, 5, 5), 31.4, 55),
    ("Dezember: 2/2/2 kWh, keine Auffuellung -> halten beim Ladestand 85", (2, 2, 2), 0.0, 85),
    ("Strecke: 3/30/30 kWh, Sonnentage ab uebermorgen (0,7 / 0,5) geben die Nacht davor frei: 27,7 -> 25", (3, 30, 30), 72.3, 25),
])
def test_entlade_untergrenze(blueprint, tag, lage, prognose, b_stern, f_soc):
    """
    Von Hand (Herbst): morgen in heutiger Form skaliert auf 15 kWh, Profil 0,19 kWh je Slot.
    Ueberschuss-Slots 09:00-17:30 (18 Slots): 14,70 kWh x 0,92 - 18 x 0,19 = 10,10 kWh Zwischenmaximum
    am Ende des PV-Tags; Tag 3/4 mit 5 kWh bleiben negativ. 10,10 / 32,15 = 31,4 %.
    Strecke: morgen ohne Ueberschuss, Tag 3 (0,7) 14,38 kWh, dessen Abend/Nacht -6,4 kWh,
    Tag 4 (0,5) +15,3 kWh bis zum Abend -> 23,24 kWh am Ende von Tag 4 = 72,3 %.
    """
    ctx = szenario(blueprint, zeit(tag, 21, 0), soc=85.0, prognose_tage_kwh=prognose).auswerten(bis="tou_schreiben")
    assert ctx["entlade_aktiv"] is True
    assert ctx["b_stern_pct"] == pytest.approx(b_stern, abs=0.1), lage
    assert ctx["f_soc"] == f_soc, lage
    ref = plan_referenz(zeit(tag, 21, 0), tage_aus_szenario(zeit(tag, 21, 0), prognose), [0.19] * 48,
                        kap_kwh=ctx["batterie_kapazitaet"], soc=85.0)
    assert ctx["b_stern_pct"] == pytest.approx(ref["b_pct"], abs=0.01) and ctx["f_soc"] == ref["f_soc"], lage


@pytest.mark.parametrize("soc, tou, erwartet", [
    (95.0, 20, False),   # weit ueber der Grenze: Register bleibt, bis es wirken kann
    (63.0, 20, False),   # 63 > 60 + 2: noch ein Lauf zu frueh
    (62.0, 20, True),    # 62 <= 60 + 2: jetzt schreiben, die Nacht laeuft auf die Grenze zu
    (62.0, 57, False),   # Aenderung 60 - 57 = 3 < 5 Punkte
])
def test_untergrenze_wird_nur_beim_annaehern_geschrieben(blueprint, tag, soc, tou, erwartet):
    """Morgen 14 kWh: Rohwert 61,4 -> Plan 60; der Ladestand liegt in allen Faellen ueber dem Rohwert (kein Halten)."""
    h = szenario(blueprint, zeit(tag, 21, 0), soc=soc, prognose_tage_kwh=(14, 5, 5), zustands_overrides=_tou(tou))
    ctx = h.auswerten(bis="tou_schreiben")
    assert 61 < ctx["f_roh"] < 62 and ctx["halten_fall"] is False
    assert ctx["f_soc"] == 60
    assert ctx["tou_schreiben"] is erwartet


def test_untergrenze_liegt_auf_dem_5er_raster(blueprint, tag):
    """Strecke: Einspeise-Kandidat 27,7 -> abgerundet 25, nicht 27; Halten bleibt am Ladestand (83), nicht 80."""
    ctx = szenario(blueprint, zeit(tag, 21, 0), soc=85.0, prognose_tage_kwh=(3, 30, 30)).auswerten(bis="f_soc")
    assert ctx["f_roh"] == pytest.approx(27.7, abs=0.1)
    assert ctx["f_soc"] == 25
    ctx = szenario(blueprint, zeit(tag, 21, 0), soc=83.0, prognose_tage_kwh=(2, 2, 2)).auswerten(bis="f_soc")
    assert ctx["f_roh"] == pytest.approx(90.0, abs=0.05)
    assert ctx["f_soc"] == 83


def _leistung(watt):
    from conftest import fake_entity
    e = fake_entity("battery_power_sensor", "sensor")
    return {e: Zustand(e, str(watt))}


def _tagesmax(soc):
    from conftest import fake_entity
    e = fake_entity("helper_max_soc_heute", "input_number")
    return {e: Zustand(e, str(soc))}


def test_halten_wird_erst_beim_entladen_geschrieben(blueprint, tag):
    """Dezember: Untergrenze 90 ueber dem Ladestand 85 -> Grenze = 85. Geschrieben, sobald die Batterie entlaedt; beim Laden nicht."""
    laedt = szenario(blueprint, zeit(tag, 21, 0), soc=85.0, prognose_tage_kwh=(2, 2, 2), zustands_overrides=_tou(20)).auswerten(bis="tou_schreiben")
    assert laedt["f_roh"] == pytest.approx(90.0, abs=0.05) and laedt["f_soc"] == 85 and laedt["halten_fall"] is True
    assert laedt["batterie_leistung"] == -1500 and laedt["tou_schreiben"] is False
    # Entladung mit 320 W, aber der Ladestand steht noch auf dem Tageshoechststand: ein Anlaufstoss, kein Halten
    stoss = szenario(blueprint, zeit(tag, 21, 0), soc=85.0, prognose_tage_kwh=(2, 2, 2), zustands_overrides={**_tou(20), **_leistung(320)}).auswerten(bis="tou_schreiben")
    assert stoss["entlaedt_nachhaltig"] is False and stoss["tou_schreiben"] is False
    # Ladestand 0.5 Punkte unter dem Tageshoechststand: die Entladung hat begonnen
    entlaedt = szenario(blueprint, zeit(tag, 21, 0), soc=85.0, prognose_tage_kwh=(2, 2, 2), zustands_overrides={**_tou(20), **_leistung(320), **_tagesmax(86)}).auswerten(bis="tou_schreiben")
    assert entlaedt["entlaedt_nachhaltig"] is True and entlaedt["tou_schreiben"] is True
    # Kein Halten-Fall (Plan 60 unter dem Ladestand 62): Schreiben haengt nicht an der Entladung
    plan = szenario(blueprint, zeit(tag, 21, 0), soc=62.0, prognose_tage_kwh=(14, 5, 5), zustands_overrides=_tou(20)).auswerten(bis="tou_schreiben")
    assert plan["halten_fall"] is False and plan["f_soc"] == 60 and plan["tou_schreiben"] is True


def test_untergrenze_wird_nicht_nachgezogen_wenn_der_wechselrichter_nicht_haelt(blueprint, tag):
    """Register 70, Ladestand 64.5 und die Batterie entlaedt: der Wechselrichter haelt nicht, die Grenze bleibt."""
    unten = szenario(blueprint, zeit(tag, 2, 0), soc=64.5, prognose_tage_kwh=(2, 2, 2), zustands_overrides={**_tou(70), **_leistung(400)}).auswerten(bis="tou_schreiben")
    assert unten["f_soc"] == 55 and unten["unter_register"] is True and unten["tou_schreiben"] is False
    # Ladestand am Register (Toleranz 1.5): Absenken auf den neuen Plan 45 bleibt erlaubt
    plan = szenario(blueprint, zeit(tag, 2, 0), soc=74.0, prognose_tage_kwh=(30, 30, 30), zustands_overrides={**_tou(75), **_leistung(400)}).auswerten(bis="tou_schreiben")
    assert plan["f_soc"] < 75 and plan["unter_register"] is False and plan["tou_schreiben"] is True


def test_trigger_untergrenze_verletzt(blueprint, tag):
    """Feuert bei Ladestand mehr als 3 Punkte unter dem Register und Entladung ueber 100 W."""
    from conftest import fake_entity
    trig = next(t for t in blueprint["trigger"] if t.get("id") == "untergrenze_verletzt")
    assert trig["for"] == "00:10:00"
    soc, tou, p = fake_entity("battery_soc_sensor", "sensor"), fake_entity("wr_tou_1", "number"), fake_entity("battery_power_sensor", "sensor")
    tv = {"tv_battery_soc": soc, "tv_wr_tou_1": tou, "tv_battery_power": p}
    for soc_w, p_w, erwartet in ((66, 300, True), (66, -500, False), (68, 300, False), (66, 50, False)):
        h = szenario(blueprint, zeit(tag, 2, 0), soc=soc_w, zustands_overrides={**_tou(70), **_leistung(p_w)})
        assert h._aufloesen(trig["value_template"], dict(tv)) is erwartet, (soc_w, p_w)


def test_meldung_untergrenze_nicht_gehalten(blueprint, tag):
    h = szenario(blueprint, zeit(tag, 2, 0), soc=66.0, prognose_tage_kwh=(2, 2, 2), zustands_overrides={**_tou(70), **_leistung(326)})
    ctx = h.auswerten()
    ctx["trigger"] = {"id": "untergrenze_verletzt", "now": h.jetzt}
    block = next(st for st in blueprint["action"] if isinstance(st, dict) and "if" in st and "untergrenze_verletzt" in str(st["if"]))
    text = " ".join(h.render(block["then"][0]["data"]["message"], ctx).split())
    assert "Register bleibt auf 70 %" in text and "Ladestand (SOC) 66 % < Untergrenze 70 % − 3 %" in text and "326 W" in text
    assert block["then"][-1].get("stop")



def test_ohne_prognose_morgen_bleibt_die_planung_aus(blueprint, tag):
    ctx = szenario(blueprint, zeit(tag, 21, 0), soc=60.0, prognose_tage_kwh=None, zustands_overrides=_tou(20)).auswerten(bis="tou_schreiben")
    assert ctx["entlade_aktiv"] is False
    assert ctx["prognose_tage"] == []
    assert ctx["tou_schreiben"] is False


def test_zellausgleich_faellig_hebt_das_ziel_auf_100(blueprint, tag):
    """Herbst-Lage, Batterie seit 12 Tagen nicht voll: Ziel 100 % -> Untergrenze 100 - 31,4 = 68,6 -> 65."""
    from conftest import fake_entity
    eid = fake_entity("json_tracking_sensor", "input_text")
    liste = json.dumps([(tag - dt.timedelta(days=d)).isoformat() for d in (12, 13, 14)])
    ctx = szenario(blueprint, zeit(tag, 21, 0), soc=85.0, prognose_tage_kwh=(15, 5, 5),
                   zustands_overrides={eid: Zustand(eid, liste)}).auswerten(bis="tou_schreiben")
    assert ctx["tage_seit_voll"] == 12
    assert ctx["zellausgleich_faellig"] is True
    assert ctx["ziel_soc_eff"] == 100
    assert ctx["f_soc"] == 65


# --------------------------------------------------------------------------
# Erwartete Einspeisespitze: Grenze bei 90 % der Peak-Shaving-Schwelle
# --------------------------------------------------------------------------
@pytest.mark.parametrize("schwelle_w, erwartet", [
    (6500, False),   # Kurve mit 3,6 kW Spitze minus 0,38 kW Haus = 3,2 kW < 5,85 kW
    (3500, True),    # 3,2 kW >= 3,15 kW
    (3600, False),   # 3,2 kW <  3,24 kW
])
def test_spitze_erwartet_an_der_90_prozent_grenze(blueprint, tag, schwelle_w, erwartet):
    ctx = szenario(blueprint, zeit(tag, 9, 0), input_overrides={"schwelle_peak_shaving": schwelle_w}).auswerten(bis="spitze_erwartet")
    assert ctx["spitze_erwartet_w"] == pytest.approx(3600 - 0.19 * 2000, abs=1)
    assert ctx["spitze_erwartet"] is erwartet


# --------------------------------------------------------------------------
# Fehleinschaetzung: Blockade lief, Spitze blieb aus
# --------------------------------------------------------------------------
@pytest.mark.parametrize("beendet, timer_tage_alt, erwartet", [
    ("on", 1, True),    # Blockade lief, Peak-Timer zuletzt gestern angefasst -> keine Spitze heute
    ("on", 0, False),   # Peak-Timer heute gestartet -> Spitze kam
    ("off", 1, False),  # keine Blockade -> nichts zu beurteilen
])
def test_blockade_ohne_spitze(blueprint, tag, beendet, timer_tage_alt, erwartet):
    import datetime as _dt
    from conftest import fake_entity
    h = szenario(blueprint, zeit(tag, 20, 0))
    h.states.tabelle[h.inputs["helper_blockade_beendet"]].state = beendet
    eid = h.inputs["helper_timer_peak"]
    h.states.tabelle[eid] = Zustand(eid, "idle", {}, last_changed=zeit(tag, 12, 0) - _dt.timedelta(days=timer_tage_alt))
    ctx = h.auswerten(bis="blockade_ohne_spitze")
    assert ctx["peak_timer_heute"] is (timer_tage_alt == 0)
    assert ctx["blockade_ohne_spitze"] is erwartet


def test_horizont_rollt_ohne_sprung_ueber_mitternacht(blueprint, tag):
    """
    Dieselben drei Tage vor und nach Mitternacht: 23:30 sieht morgen (14,66 kWh, heutige Form),
    Tag 3 (15) und Tag 4 (10); 00:00 sieht heute (14,66 aus dem Array), morgen (15), Tag 3 (10).
    Die Gewichte haengen am Vorlauf, nicht am Kalendertag: beide Laeufe rechnen dieselbe Untergrenze.
    """
    abend = szenario(blueprint, zeit(tag, 23, 30), soc=85.0, prognose_tage_kwh=(14.663, 15, 10)).auswerten(bis="f_soc")
    nacht = szenario(blueprint, zeit(tag + dt.timedelta(days=1), 0, 0), soc=85.0, prognose_tage_kwh=(15, 10, 5)).auswerten(bis="f_soc")
    assert [t["name"] for t in abend["prognose_tage"]] == ["morgen", "Tag 3", "Tag 4"]
    assert [t["name"] for t in nacht["prognose_tage"]] == ["heute", "morgen", "Tag 3"]
    assert [t["vertrauen"] for t in abend["prognose_tage"]] == [1.0, 0.7, 0.5]
    assert [t["vertrauen"] for t in nacht["prognose_tage"]] == [1.0, 0.7, 0.5]
    assert abend["b_stern_pct"] == pytest.approx(nacht["b_stern_pct"], abs=0.05)
    assert abend["f_roh"] == pytest.approx(nacht["f_roh"], abs=0.05)
    # Nach Mitternacht zaehlt der heutige PV-Tag ab dem ersten Ueberschuss-Slot (09:00), 9,80 kWh
    # Zwischenmaximum bei Sonnenuntergang = 30,5 % von 32,15 kWh; dunkle Folgetage aendern nichts.
    frueh = szenario(blueprint, zeit(tag, 3, 0), soc=85.0, prognose_tage_kwh=(2, 2, 2)).auswerten(bis="f_soc")
    assert frueh["plan_start"].endswith("09:00:00+02:00")
    assert frueh["b_stern_pct"] == pytest.approx(30.5, abs=0.1) and frueh["f_soc"] == 55


def test_vertrauen_daempft_den_ueberschuss_nicht_die_pv(blueprint, tag):
    """
    Morgen ohne PV, Tag 3 mit 12 kWh gegen 9,12 kWh Tagesverbrauch. Auf die Brutto-PV gerechnet
    (12 x 0,7 x 0,92 = 7,7 < 9,1) bliebe nichts uebrig; auf den Ueberschuss der Slots gerechnet zaehlt
    der Tag mit genau 70 % seines Plus: B* bei Vertrauen 0,7 = 0,7 x B* bei Vertrauen 1,0.
    """
    voll = szenario(blueprint, zeit(tag, 21, 0), soc=85.0, prognose_tage_kwh=(0, 12, 0),
                    input_overrides={"vertrauen_tag3": 1.0}).auswerten(bis="f_soc")
    gedaempft = szenario(blueprint, zeit(tag, 21, 0), soc=85.0, prognose_tage_kwh=(0, 12, 0)).auswerten(bis="f_soc")
    assert gedaempft["prognose_tage"][0]["name"] == "Tag 3" and gedaempft["plan_start"].endswith("09:00:00+02:00")
    assert voll["b_stern_pct"] > 15
    assert gedaempft["b_stern_pct"] == pytest.approx(0.7 * voll["b_stern_pct"], abs=0.01)
    ref = plan_referenz(zeit(tag, 21, 0), tage_aus_szenario(zeit(tag, 21, 0), (0, 12, 0)), [0.19] * 48, kap_kwh=gedaempft["batterie_kapazitaet"])
    assert gedaempft["b_stern_pct"] == pytest.approx(ref["b_pct"], abs=0.01)


def test_defizit_vor_der_sonnenstrecke_mindert_die_auffuellung_nicht(blueprint, tag):
    """
    Dunkle Tage vor einer Sonnenstrecke traegt die Untergrenze selbst, sie zaehlen nicht gegen die
    Auffuellung; ein marginaler Ueberschuss-Slot am dunklen Tag aendert daher nichts.
    Von Hand: Tag 4 mit 30 kWh (Gewicht 0,5) hat 20 Ueberschuss-Slots 08:00-17:30, Summe der
    Netto-Werte 23,80 kWh x 0,5 = 11,90 kWh = 37,0 % von 32,15 kWh -> 100 - 37 = 63 > 50 -> f_roh 53,0 -> 50.
    Mit morgen 15 kWh (Lauf 10,10 kWh) davor gilt das groessere Zwischenmaximum, wieder 11,90 kWh.
    """
    dunkel = szenario(blueprint, zeit(tag, 21, 0), soc=85.0, prognose_tage_kwh=(2, 2, 30)).auswerten(bis="f_soc")
    knapp = szenario(blueprint, zeit(tag, 21, 0), soc=85.0, prognose_tage_kwh=(1.9, 2, 30)).auswerten(bis="f_soc")
    assert dunkel["b_stern_pct"] == pytest.approx(37.0, abs=0.1) and dunkel["f_soc"] == 50
    assert knapp["b_stern_pct"] == pytest.approx(dunkel["b_stern_pct"], abs=0.01) and knapp["f_soc"] == dunkel["f_soc"]
    assert dunkel["plan_start"].endswith("08:00:00+02:00") and dunkel["plan_start"][:10] == (tag + dt.timedelta(days=3)).isoformat()
    tal = szenario(blueprint, zeit(tag, 21, 0), soc=85.0, prognose_tage_kwh=(15, 2, 30)).auswerten(bis="f_soc")
    assert tal["b_stern_pct"] == pytest.approx(37.0, abs=0.1)
    assert [t["name"] for t in tal["prognose_tage"]] == ["morgen", "Tag 3", "Tag 4"]
    assert tal["prognose_tage"][0]["bilanz"] == pytest.approx(7.82, abs=0.05) and tal["prognose_tage"][1]["bilanz"] < 0
    # Ohne jeden Ueberschuss: kein Zaehlbeginn, keine Tage in der Meldung
    keiner = szenario(blueprint, zeit(tag, 21, 0), soc=85.0, prognose_tage_kwh=(2, 2, 2)).auswerten(bis="f_soc")
    assert keiner["plan_start"] is None and keiner["prognose_tage"] == [] and keiner["f_soc"] == 85


def test_folgetag_mit_eigenen_slots_statt_heutiger_form(blueprint, tag):
    """Traegt der Morgen-Sensor detailedForecast, zaehlt dessen Verlauf; ohne Attribut die heutige Form."""
    from conftest import fake_entity, prognose_gleichmaessig
    eid = fake_entity("solcast_morgen_sensor", "sensor")
    morgen = tag + dt.timedelta(days=1)
    # 12 kWh gleichmaessig 10:00-16:00 (12 Slots x 2 kW x 0,5 h), P10 = P50
    slots = prognose_gleichmaessig(morgen, 2.0, von=dt.time(10, 0), slots=12)
    for sl in slots:
        sl["pv_estimate10"] = sl["pv_estimate"]
    mit = szenario(blueprint, zeit(tag, 21, 0), soc=85.0, prognose_tage_kwh=(12, 2, 2),
                   zustands_overrides={eid: Zustand(eid, "12.0", {"estimate10": 12.0, "detailedForecast": slots})}).auswerten(bis="f_soc")
    ohne = szenario(blueprint, zeit(tag, 21, 0), soc=85.0, prognose_tage_kwh=(12, 2, 2)).auswerten(bis="f_soc")
    eintrag = next(t for t in mit["solcast_slot_tage"] if t["datum"] == morgen.isoformat())
    assert eintrag["slots"] is True and sum(eintrag["k"]) == pytest.approx(12.0, abs=0.01)
    # 12 Slots x (1,0 x 0,92 - 0,19) = 8,76 kWh Zwischenmaximum = 27,2 % von 32,15 kWh
    assert mit["b_stern_pct"] == pytest.approx(8.76 / mit["batterie_kapazitaet"] * 100, abs=0.05)
    assert ohne["b_stern_pct"] != pytest.approx(mit["b_stern_pct"], abs=0.5)


def test_solcast_sensoren_noch_nicht_gerollt(blueprint, tag):
    """
    00:15 nach Mitternacht, die Solcast-Sensoren zeigen noch den Vortag: der Heute-Sensor traegt das Array
    von gestern, 'morgen' ist bereits heute. Die Zuordnung ueber das Datum des Arrays haelt die Planung
    gleich, als waeren die Sensoren gerollt.
    """
    from conftest import fake_entity, prognose_real
    heute = tag + dt.timedelta(days=1)
    jetzt = zeit(heute, 0, 15)
    gerollt = szenario(blueprint, jetzt, soc=85.0, prognose_tage_kwh=(15, 10, 5)).auswerten(bis="f_soc")
    e_heute = fake_entity("solcast_heute_sensor", "sensor")
    alt = prognose_real(tag)  # Array von gestern
    verzoegert = szenario(blueprint, jetzt, soc=85.0, prognose_tage_kwh=(14.663, 15, 10),
                          zustands_overrides={e_heute: Zustand(e_heute, "17.65", {"detailedForecast": alt})}).auswerten(bis="f_soc")
    assert [t["datum"] for t in verzoegert["solcast_slot_tage"]][0] == tag.isoformat()
    assert verzoegert["b_stern_pct"] == pytest.approx(gerollt["b_stern_pct"], abs=0.05)
    assert verzoegert["f_soc"] == gerollt["f_soc"]


# --------------------------------------------------------------------------
# Netzbezug im Halten: Verlust nur, was nachts bezogen UND tags eingespeist wurde
# --------------------------------------------------------------------------
def _halten(bezug, export_heute):
    from conftest import fake_entity
    e1 = fake_entity("helper_halten_bezug", "input_number"); e2 = fake_entity("grid_export_kwh_heute", "sensor")
    return {e1: Zustand(e1, str(bezug)), e2: Zustand(e2, str(export_heute))}


@pytest.mark.parametrize("bezug, export_heute, verlust, grund", [
    (3.0, 12.5, 3.0, "Netzbezug im Halten deckelt"),
    (20.0, 5.0, 5.0, "Tageseinspeisung deckelt"),
    (3.0, 0.0, 0.0, "voll geworden ohne jede Einspeisung: nichts verloren"),
    (0.0, 12.5, 0.0, "kein Verbrauch nach Erreichen der Grenze"),
])
def test_halten_verlust_zweifacher_deckel(blueprint, tag, bezug, export_heute, verlust, grund):
    ctx = szenario(blueprint, zeit(tag, 20, 0), zustands_overrides=_halten(bezug, export_heute)).auswerten(bis="halten_verlust_kwh")
    assert ctx["halten_verlust_kwh"] == pytest.approx(verlust, abs=0.001), grund


def test_halten_verlust_ohne_helfer_null(blueprint, tag):
    ctx = szenario(blueprint, zeit(tag, 20, 0), input_overrides={"helper_halten_bezug": ""}).auswerten(bis="halten_verlust_kwh")
    assert ctx["halten_kwh"] == 0 and ctx["halten_verlust_kwh"] == 0


def _update_json_zweig(blueprint, h, trigger_zeit):
    """Loest die Variablen des update_json-Zweigs auf, wie der Lauf es taete (verschachtelte if-Zweige eingeschlossen)."""
    ctx = h.auswerten(bis="log_kopf")   # globale Variablen vor dem Zweig, wie im Lauf
    ctx["trigger"] = {"id": "update_json", "now": trigger_zeit}
    block = next(s for s in blueprint["action"] if isinstance(s, dict) and "if" in s and "update_json" in str(s["if"]))
    def wahr(bedingungen):
        return all(h._aufloesen(b["value_template"], ctx) for b in bedingungen if b.get("condition") == "template")
    def gehe(schritte):
        for st in schritte:
            if "variables" in st:
                for k, v in st["variables"].items():   # der Reihe nach, wie HA: spaetere Keys sehen fruehere
                    ctx[k] = h._aufloesen(v, ctx)
            elif "if" in st and wahr(st["if"]):
                gehe(st["then"])
    gehe(block["then"])
    return ctx


@pytest.mark.parametrize("bisher, slot_kwh, soc, tou, erwartet", [
    (2.0, 0.6, 65.0, 65, 2.6),        # Halten: Slot-Verbrauch kommt dazu
    (14.2, 0.6, 65.0, 65, 14.469),    # Deckel: (65-20) % x 32.154 kWh (2 x 314 Ah x 51.2 V) zurueckgehalten
    (2.0, 0.6, 70.0, 65, None),       # Batterie entlaedt noch: kein Halten, nichts geschrieben
])
def test_netzbezug_im_halten_laeuft_im_update_json_zweig_auf(blueprint, tag, bisher, slot_kwh, soc, tou, erwartet):
    from conftest import fake_entity
    tous = {fake_entity(f"wr_tou_{i}", "number"): Zustand(fake_entity(f"wr_tou_{i}", "number"), str(tou)) for i in range(1, 7)}
    e = fake_entity("helper_halten_bezug", "input_number"); tous[e] = Zustand(e, str(bisher))
    h = szenario(blueprint, zeit(tag, 3, 0), soc=soc, hausverbrauch_slot_kwh=slot_kwh, zustands_overrides=tous, trigger_id="update_json")
    ctx = _update_json_zweig(blueprint, h, zeit(tag, 3, 0))
    assert ctx["hb_lage"] is (erwartet is not None) and ctx["hb_haelt"] is (erwartet is not None)
    if erwartet is not None:
        assert ctx["hb_neu"] == pytest.approx(erwartet, abs=0.001)


@pytest.mark.parametrize("stunde, soc, tou, erwartet", [
    (3, 65.0, 65, True),    # nachts, Ladestand an der Grenze, Grenze ueber dem Minimum
    (3, 66.0, 65, True),    # ein Punkt darueber zaehlt noch als Halten
    (3, 70.0, 65, False),   # Batterie entlaedt noch
    (3, 20.0, 20, False),   # allgemeines Minimum ist keine Planungsgrenze
    (12, 65.0, 65, False),  # tagsueber kein Halten
])
def test_halten_aktiv_nur_wenn_die_grenze_schlagend_ist(blueprint, tag, stunde, soc, tou, erwartet):
    from conftest import fake_entity
    tous = {fake_entity(f"wr_tou_{i}", "number"): Zustand(fake_entity(f"wr_tou_{i}", "number"), str(tou)) for i in range(1, 7)}
    ctx = szenario(blueprint, zeit(tag, stunde, 0), soc=soc, zustands_overrides=tous).auswerten(bis="halten_aktiv")
    assert ctx["halten_aktiv"] is erwartet


def test_slot_zeile_der_aufzeichnung(blueprint, tag):
    """Der Profil-Lauf schreibt Slot-Verbrauch und Halte-Lage, auch ohne zugewiesenen Helfer."""
    import json
    from conftest import fake_entity
    tous = {fake_entity(f"wr_tou_{i}", "number"): Zustand(fake_entity(f"wr_tou_{i}", "number"), "65") for i in range(1, 7)}
    h = szenario(blueprint, zeit(tag, 3, 0), soc=65.0, hausverbrauch_slot_kwh=0.6, zustands_overrides=tous,
                 input_overrides={"helper_halten_bezug": ""}, trigger_id="update_json")
    ctx = _update_json_zweig(blueprint, h, zeit(tag, 3, 0))
    block = next(s for s in blueprint["action"] if isinstance(s, dict) and "if" in s and "update_json" in str(s["if"]))
    schritt = next(st for st in block["then"] if "if" in st and "pv_optimizer_aufzeichnung" in str(st["if"])
                   and "'slot'" in str(st["then"]))
    nachricht = h._aufloesen(next(a for a in schritt["then"] if a.get("action") == "notify.send_message")["data"]["message"], ctx)
    zeile = nachricht if isinstance(nachricht, dict) else json.loads(nachricht)
    assert zeile["art"] == "slot" and zeile["halten"] is True and zeile["slot_kwh"] == pytest.approx(0.6)
    assert "notstrom_kwh" in zeile
    assert zeile["tou_ist"] == 65 and zeile["zurueckgehalten_kwh"] == pytest.approx(14.469, abs=0.001)
    assert zeile["halten_bezug_kwh"] is None and "entitaeten" not in zeile
    assert zeile["kennung"] == ctx["lauf_kennung"]
    assert ctx["hb_lage"] is True and ctx["hb_haelt"] is False


# --------------------------------------------------------------------------
# Aufzeichnung: Entscheidungszeile nur, wenn der Lauf etwas geschrieben hat
# --------------------------------------------------------------------------
@pytest.mark.parametrize("geaendert_vor_s, erwartet", [
    (-30, True),    # Ladestrom-Register 30 s nach Laufstart geaendert -> Entscheidung
    (600, False),   # zuletzt 10 min vor dem Lauf geaendert -> nichts passiert
])
def test_entscheidung_im_lauf_ueber_last_changed(blueprint, tag, geaendert_vor_s, erwartet):
    import datetime as _dt
    from conftest import fake_entity
    jetzt = zeit(tag, 10, 0)
    e = fake_entity("wr_max_charge_current", "number")
    h = szenario(blueprint, jetzt, zustands_overrides={e: Zustand(e, "36", {}, last_changed=jetzt - _dt.timedelta(seconds=geaendert_vor_s))})
    ctx = h.auswerten(bis="entscheidung_im_lauf")
    assert ctx["entscheidung_im_lauf"] is erwartet


def test_trend_zusatz_nur_wenn_der_realitaets_check_kuerzt(blueprint, tag):
    ctx = szenario(blueprint, zeit(tag, 10, 0)).auswerten(bis="trend_log_addon")
    if ctx["trend_aktiv"] and ctx["pv_abschlag"] < 1.0:
        assert ctx["trend_log_addon"].startswith("Realitäts-Check kürzt")
    else:
        assert ctx["trend_log_addon"] == ""


# --------------------------------------------------------------------------
# Live-Anschluss des Verbrauchsprofils
# --------------------------------------------------------------------------
@pytest.mark.parametrize("minute, slot_kwh, erwartet_slot0", [
    (20, 0.6, 0.57),    # 0.6 kWh in 20 min -> 0.9 kWh/Slot, gedeckelt auf 3 x 0.19 = 0.57
    (20, 0.2, 0.30),    # 0.2 kWh in 20 min -> 0.30 kWh/Slot, unter dem Deckel
    (10, 0.6, None),    # unter 15 Minuten: kein Anschluss, reines Profil
])
def test_verbrauchsprofil_live_anschluss(blueprint, tag, minute, slot_kwh, erwartet_slot0):
    ctx = szenario(blueprint, zeit(tag, 10, minute), hausverbrauch_slot_kwh=slot_kwh).auswerten()
    idx = 20
    rein = ctx["json_profil"]; live = ctx["json_profil_live"]
    if erwartet_slot0 is None:
        assert ctx["haus_live_kwh"] == -1 and live == rein
    else:
        assert live[idx] == pytest.approx(erwartet_slot0, abs=0.005)
        # Auslauf ueber vier Slots: Gewichte 1, 3/4, 1/2, 1/4, danach reines Profil
        for k, w in ((1, 0.75), (2, 0.5), (3, 0.25)):
            assert live[idx + k] == pytest.approx(rein[idx + k] * (1 - w) + erwartet_slot0 * w, abs=0.005)
        assert live[idx + 4] == rein[idx + 4] and live[idx - 1] == rein[idx - 1]
        assert ctx["verbrauch_tag_kwh"] == pytest.approx(sum(map(float, rein)), abs=0.001)


# --------------------------------------------------------------------------
# Voraussichtliche Zeiten: voll (Plan / voller Strom) und Untergrenze erreicht
# --------------------------------------------------------------------------
def test_untergrenze_um_aus_dem_profil(blueprint, tag):
    """21:00, Ladestand 60 %, Register 50 %: 10 % von 32.15 kWh = 3.215 kWh / 0.19 kWh je Slot = 16.9 Slots -> 05:27."""
    from conftest import fake_entity
    tous = {fake_entity(f"wr_tou_{i}", "number"): Zustand(fake_entity(f"wr_tou_{i}", "number"), "50") for i in range(1, 7)}
    ctx = szenario(blueprint, zeit(tag, 21, 0), soc=60.0, zustands_overrides=tous).auswerten(bis="untergrenze_text")
    assert ctx["untergrenze_um"] == "um 05:27 Uhr"
    assert ctx["untergrenze_text"] == "Untergrenze 50 % voraussichtlich um 05:27 Uhr erreicht."
    # Ladestand an der Grenze, dunkle Folgetage -> halten bei 50, nichts zu schreiben, Grenze ist jetzt erreicht
    tief = szenario(blueprint, zeit(tag, 21, 0), soc=50.0, prognose_tage_kwh=(2, 2, 2), zustands_overrides=tous).auswerten(bis="untergrenze_text")
    assert tief["untergrenze_wirksam"] == 50 and tief["untergrenze_um"] == "jetzt" and tief["untergrenze_text"] == ""
    # Register 50, Plan 40 liegt darunter und wird geschrieben: die Schaetzung gilt fuer die neue Grenze
    neu = szenario(blueprint, zeit(tag, 21, 0), soc=50.0, prognose_tage_kwh=(24, 5, 5), zustands_overrides=tous).auswerten(bis="untergrenze_text")
    assert neu["tou_schreiben"] is True and neu["f_soc"] == 40
    assert neu["untergrenze_wirksam"] == neu["f_soc"] and neu["untergrenze_um"].startswith("um 0")
    tag_ctx = szenario(blueprint, zeit(tag, 10, 0), soc=60.0, zustands_overrides=tous).auswerten(bis="untergrenze_text")
    assert tag_ctx["untergrenze_um"] == "" and tag_ctx["untergrenze_text"] == "", "tags keine Schaetzung"


def test_voll_um_liegt_im_ladefenster(blueprint, tag):
    """Plan-Vollzeit = Fensterende minus Vorlauf; bei vollem Strom frueher oder gleich, beide vor Sonnenuntergang."""
    import re as _re
    ctx = szenario(blueprint, zeit(tag, 10, 0), soc=85.0).auswerten(bis="fallb_voll_um")
    for k in ("plan_voll_um", "fallb_voll_um"):
        assert _re.fullmatch(r"um \d\d:\d\d Uhr", ctx[k]), ctx[k]
    plan = ctx["plan_voll_um"][3:8]; voll = ctx["fallb_voll_um"][3:8]
    assert "10:00" < voll <= plan <= "21:00", (voll, plan)


# --------------------------------------------------------------------------
# Wallbox / evcc: Autos laden zuerst, ihre Ladung gehoert nicht ins Profil
# --------------------------------------------------------------------------
def test_auto_bedarf_geht_vom_ueberschuss_der_ersten_slots_ab(blueprint, tag):
    from conftest import fake_entity
    e = fake_entity("ev_restbedarf_sensor", "sensor")
    ohne = szenario(blueprint, zeit(tag, 10, 0)).auswerten(bis="brutto_ueberschuss_rest")
    mit = szenario(blueprint, zeit(tag, 10, 0), zustands_overrides={e: Zustand(e, "5.0")}).auswerten(bis="brutto_ueberschuss_rest")
    assert ohne["ev_bedarf_kwh"] == 0 and mit["ev_bedarf_kwh"] == 5.0
    assert mit["brutto_ueberschuss_rest"] == pytest.approx(ohne["brutto_ueberschuss_rest"] - 5.0, abs=0.01)
    # Die fruehesten Slots tragen den Abzug, der letzte bleibt unveraendert.
    assert mit["rest_slots"][-1][0] == ohne["rest_slots"][-1][0]
    assert mit["rest_slots"][0][0] < ohne["rest_slots"][0][0]
    # Bedarf ueber dem ganzen Ueberschuss: nichts fuer die Batterie, kein negativer Slot, kein Nachladebedarf daraus.
    viel = szenario(blueprint, zeit(tag, 10, 0), zustands_overrides={e: Zustand(e, "99")}).auswerten(bis="brutto_ueberschuss_rest")
    assert viel["brutto_ueberschuss_rest"] == 0 and viel["rest_slots"] == [] and viel["defizit_kwh"] == ohne["defizit_kwh"]
    # Ohne zugewiesenes Feld: kein Abzug.
    leer = szenario(blueprint, zeit(tag, 10, 0), input_overrides={"ev_restbedarf_sensor": []}).auswerten(bis="brutto_ueberschuss_rest")
    assert leer["ev_bedarf_kwh"] == 0 and leer["brutto_ueberschuss_rest"] == ohne["brutto_ueberschuss_rest"]
    # Zwei Ladepunkte: die Werte werden addiert, ein fehlender Sensor zaehlt 0.
    e2 = "sensor.ladepunkt_2_rest"
    zwei = szenario(blueprint, zeit(tag, 10, 0), input_overrides={"ev_restbedarf_sensor": [e, e2, "sensor.gibt_es_nicht"]},
                    zustands_overrides={e: Zustand(e, "5.0"), e2: Zustand(e2, "2.5")}).auswerten(bis="ev_bedarf_kwh")
    assert zwei["ev_bedarf_kwh"] == 7.5


def test_wallbox_ladung_bleibt_aus_profil_und_live_anschluss(blueprint, tag):
    from conftest import fake_entity
    wb = fake_entity("wallbox_kwh_sensor", "sensor")
    h = szenario(blueprint, zeit(tag, 14, 0), hausverbrauch_slot_kwh=0.9, zustands_overrides={wb: Zustand(wb, "0.6")}, trigger_id="update_json")
    ctx = _update_json_zweig(blueprint, h, zeit(tag, 14, 0))
    assert ctx["wallbox_slot_kwh"] == 0.6 and ctx["last_half_hour_kwh"] == pytest.approx(0.3)
    # Zeitversatz der Zaehler: nie negativ.
    h2 = szenario(blueprint, zeit(tag, 14, 0), hausverbrauch_slot_kwh=0.2, zustands_overrides={wb: Zustand(wb, "0.5")}, trigger_id="update_json")
    assert _update_json_zweig(blueprint, h2, zeit(tag, 14, 0))["last_half_hour_kwh"] == 0
    # Live-Anschluss: 0.9 kWh nach 20 Minuten, davon 0.6 Wallbox -> 0.3 / (20/30) = 0.45 kWh je Slot.
    live = szenario(blueprint, zeit(tag, 14, 20), hausverbrauch_slot_kwh=0.9, zustands_overrides={wb: Zustand(wb, "0.6")}).auswerten(bis="haus_live_kwh")
    assert live["haus_live_kwh"] == pytest.approx(0.45, abs=0.005)
    # Ohne Feld: der volle Zaehler.
    h3 = szenario(blueprint, zeit(tag, 14, 0), hausverbrauch_slot_kwh=0.9, input_overrides={"wallbox_kwh_sensor": []}, trigger_id="update_json")
    assert _update_json_zweig(blueprint, h3, zeit(tag, 14, 0))["last_half_hour_kwh"] == pytest.approx(0.9)
    # Zwei Wallboxen: Summe, und beide Zaehler werden genullt.
    wb2 = "sensor.wallbox_2_halbstuendlich"
    h4 = szenario(blueprint, zeit(tag, 14, 0), hausverbrauch_slot_kwh=0.9, input_overrides={"wallbox_kwh_sensor": [wb, wb2]},
                  zustands_overrides={wb: Zustand(wb, "0.2"), wb2: Zustand(wb2, "0.3")}, trigger_id="update_json")
    ctx4 = _update_json_zweig(blueprint, h4, zeit(tag, 14, 0))
    assert ctx4["wallbox_slot_kwh"] == pytest.approx(0.5) and ctx4["last_half_hour_kwh"] == pytest.approx(0.4) and ctx4["wallbox_liste"] == [wb, wb2]


# --------------------------------------------------------------------------
# Zweitmeinung des evcc-Optimizers: Anfrage und Lesen der Antwort
# --------------------------------------------------------------------------
def _optimizer_zweig(blueprint, h, antwort=None):
    """Loest die Variablen des Optimizer-Laufs auf; die Antwort des rest_command wird vorgegeben."""
    ctx = h.auswerten()
    ctx["trigger"] = {"id": "optimizer_vergleich", "now": h.jetzt}
    block = next(st for st in blueprint["action"] if isinstance(st, dict) and "if" in st and "optimizer_vergleich" in str(st["if"]))
    innen = block["then"][0]["then"]
    for st in innen:
        if "variables" in st:
            for k, v in st["variables"].items():
                ctx[k] = h._aufloesen(v, ctx)
        elif st.get("response_variable") == "opt_antwort" and antwort is not None:
            ctx["opt_antwort"] = antwort
    zeile = next(st for st in innen if st.get("action") == "notify.send_message")
    return ctx, json.loads(h.render(zeile["data"]["message"], ctx))


def test_optimizer_anfrage_aus_dem_lauf(blueprint, tag):
    h = szenario(blueprint, zeit(tag, 21, 0), soc=60.0, input_overrides={"optimizer_url": "http://localhost:7050"})
    ctx, _ = _optimizer_zweig(blueprint, h, antwort={"status": 200, "content": {}})
    a = ctx["opt_anfrage"]; a = a if isinstance(a, dict) else json.loads(a)
    ts, bat = a["time_series"], a["batteries"][0]
    assert len(ts["ft"]) == len(ts["gt"]) == len(ts["dt"]) == 48 and set(ts["dt"]) == {1800}
    assert bat["s_capacity"] == pytest.approx(ctx["batterie_kapazitaet"] * 1000)
    assert bat["s_initial"] == pytest.approx(0.6 * bat["s_capacity"])
    assert bat["s_min"] == pytest.approx(ctx["default_tou_soc"] / 100 * bat["s_capacity"])
    assert set(ts["gt"]) == {190.0}                      # Profil 0.19 kWh je Slot in Wh
    assert all(f == 0 for f in ts["ft"][:6])             # 21:00 - 00:00 keine PV
    # Morgen: die vertraute Tagessumme der Entlade-Planung (P10/P50-Mischung), nicht das rohe P50 des Sensors
    morgen = next(t["pv"] for t in ctx["prognose_tage"] if t["name"] == "morgen")
    assert sum(ts["ft"]) / 1000 == pytest.approx(morgen, rel=0.02)
    assert morgen <= float(h.states.tabelle[h.inputs["solcast_morgen_sensor"]].state)   # synthetisch P10 = P50
    # Heute: dieselbe Slot-Mischung wie slot_daten (P10/P50 mit Realitaets-Check)
    tag_ctx = szenario(blueprint, zeit(tag, 10, 0), soc=60.0, input_overrides={"optimizer_url": "http://localhost:7050"})
    tctx, _ = _optimizer_zweig(blueprint, tag_ctx, antwort={"status": 200, "content": {}})
    ta = tctx["opt_anfrage"]; ta = ta if isinstance(ta, dict) else json.loads(ta)
    anteil = tctx["blend_p50_anteil"]; ab = tctx["pv_abschlag"]
    fc = {s_["period_start"] if isinstance(s_["period_start"], str) else s_["period_start"].isoformat(): s_
          for s_ in tag_ctx.states.tabelle[tag_ctx.inputs["solcast_heute_sensor"]].attributes["detailedForecast"]}
    start = tag_ctx.jetzt.replace(minute=0, second=0, microsecond=0)
    slot = fc[(start + dt.timedelta(minutes=30)).isoformat()]
    erwartet = (slot["pv_estimate"] * anteil + slot["pv_estimate10"] * (1 - anteil)) * 0.5 * ab * 1000
    assert ta["time_series"]["ft"][1] == pytest.approx(erwartet, abs=1)
    assert ts["p_N"][0] == pytest.approx(0.0003) and ts["p_E"][0] == pytest.approx(0.00008)
    assert a["strategy"]["charging_strategy"] == "attenuate_feedin_peaks"


def test_optimizer_antwort_wird_in_zeiten_uebersetzt(blueprint, tag):
    h = szenario(blueprint, zeit(tag, 21, 0), soc=60.0, input_overrides={"optimizer_url": "http://localhost:7050"})
    kap = h.auswerten(bis="batterie_kapazitaet")["batterie_kapazitaet"] * 1000
    # Laden ab Slot 22 (08:00), Ladestand 100 % erstmals am Ende von Slot 26 -> voll um 10:30; Nacht-Minimum 45 %
    soc = [kap * 0.6] * 4 + [kap * 0.45] * 18 + [kap * 0.7] * 4 + [kap] * 22
    laden = [0.0] * 22 + [800.0] * 8 + [0.0] * 18
    antwort = {"status": 200, "content": {"status": "Optimal", "batteries": [{"state_of_charge": soc, "charging_power": laden}]}}
    ctx, zeile = _optimizer_zweig(blueprint, h, antwort=antwort)
    o = ctx["opt_auswertung"]; o = o if isinstance(o, dict) else json.loads(o)
    assert o["status"] == "Optimal" and o["laedt_ab"] == "08:00" and o["voll_um"] == "10:30" and o["nacht_min_soc"] == 45.0
    assert len(o["soc_verlauf"]) == 48 and o["soc_verlauf"][0] == 60.0
    assert zeile["art"] == "optimizer" and zeile["kennung"] == ctx["lauf_kennung"]
    assert zeile["blueprint"]["f_soc"] == ctx["f_soc"] and zeile["blueprint"]["aktueller_soc"] == 60.0
    assert zeile["anfrage"]["strategie"] == "attenuate_feedin_peaks" and zeile["anfrage"]["verbrauch_kwh"] == pytest.approx(0.19 * 48, abs=0.01)
    # Keine Antwort (rest_command fehlgeschlagen): Zeile kommt trotzdem, mit leerem Fahrplan
    ctx2, zeile2 = _optimizer_zweig(blueprint, h, antwort=None)
    assert zeile2["optimizer"]["status"] == "keine Antwort" and zeile2["optimizer"]["voll_um"] is None


def test_notstromprofil_lernt_in_wattstunden(blueprint, tag):
    """Zaehler 0,075 kWh in der Halbstunde bis 14:00 (Index 27): leerer Helfer -> 75 Wh in allen Slots; Profil 60 Wh -> 60 x 6/7 + 75 / 7 = 62."""
    from conftest import fake_entity
    z = fake_entity("notstrom_utility_sensor", "sensor"); helfer = fake_entity("notstrom_json_text", "input_text")
    h = szenario(blueprint, zeit(tag, 14, 0), zustands_overrides={z: Zustand(z, "0.075")}, trigger_id="update_json")
    ctx = _update_json_zweig(blueprint, h, zeit(tag, 14, 0))
    assert ctx["np_kwh"] == pytest.approx(0.075)
    neu = ctx["np_neu"] if isinstance(ctx["np_neu"], list) else json.loads(ctx["np_neu"])
    assert neu == [75] * 48
    h2 = szenario(blueprint, zeit(tag, 14, 0), zustands_overrides={z: Zustand(z, "0.075"), helfer: Zustand(helfer, json.dumps([60] * 48))}, trigger_id="update_json")
    ctx2 = _update_json_zweig(blueprint, h2, zeit(tag, 14, 0))
    neu2 = ctx2["np_neu"] if isinstance(ctx2["np_neu"], list) else json.loads(ctx2["np_neu"])
    assert neu2[27] == 62 and neu2[26] == 60
    # Ganzes Haus am Notstromausgang, 1,5 kWh je Halbstunde: 48 x "1500," = 240 Zeichen passen
    assert len(json.dumps([1500] * 48, separators=(",", ":"))) <= 255
    # Ohne Zaehler: kein Wert, kein Schreiben
    h3 = szenario(blueprint, zeit(tag, 14, 0), input_overrides={"notstrom_utility_sensor": ""}, trigger_id="update_json")
    ctx3 = _update_json_zweig(blueprint, h3, zeit(tag, 14, 0))
    assert ctx3["np_kwh"] is None and "np_neu" not in ctx3


# --------------------------------------------------------------------------
# Notstromreserve: Untergrenze nie unter Abschalt-Ladestand plus Defizit am Notstromausgang
# --------------------------------------------------------------------------
def _notstrom_zustaende(profil_wh, shutdown=None):
    from conftest import fake_entity
    helfer = fake_entity("notstrom_json_text", "input_text")
    zs = {helfer: Zustand(helfer, json.dumps(profil_wh))}
    if shutdown is not None:
        e = fake_entity("wr_shutdown_soc", "number")
        zs[e] = Zustand(e, str(shutdown))
    return zs


def test_notstromreserve_hebt_die_untergrenze(blueprint, tag):
    """
    21:00, gleichmaessige Prognose 2 kW 08:00-18:00 (P10 0,7), morgen/Tag 3/Tag 4 je 40 kWh: Die Planung
    fuellt die Batterie morgen ueber 100 % (B* 33 kWh = 102,6 %), Ziel- und Einspeise-Kandidat sind negativ,
    Untergrenze ohne Reserve = Minimum 20 %. Notstromlast 200 Wh je Halbstunde: 22 Nacht-Slots bis 08:00
    x (0,2 + 0,045) / 0,95 = 5,674 kWh, dann PV10 2,0 kWh x 0,92 > Last -> Defizit endet 08:00.
    Reserve = 10 % + 5,674 / 32,15 = 27,6 % -> aufgerundet 30 %; mit Abschalt-Ladestand 5 %: 22,6 % -> 25 %.
    """
    fc = prognose_gleichmaessig(tag, 2.0)
    basis = dict(forecast=fc, prognose_tage_kwh=(40, 40, 40), soc=85.0)
    ohne = szenario(blueprint, zeit(tag, 21, 0), **basis).auswerten(bis="f_soc")
    assert ohne["notstrom_profil"] is None and ohne["reserve_pct"] == 0 and ohne["reserve_plan"]["kwh"] == 0
    assert ohne["f_ziel"] < 0 and ohne["f_soc"] == 20

    mit = szenario(blueprint, zeit(tag, 21, 0), zustands_overrides=_notstrom_zustaende([200] * 48), **basis).auswerten(bis="f_soc")
    assert mit["shutdown_soc"] == 10, "ohne Entitaet 10 %"
    assert mit["reserve_plan"]["kwh"] == pytest.approx(5.674, abs=0.001)
    assert mit["reserve_plan"]["bis"].endswith(f"{(tag + dt.timedelta(days=1)).isoformat()}T08:00:00+02:00")
    assert mit["reserve_pct"] == pytest.approx(27.648, abs=0.01)
    assert mit["reserve_5"] == 30 and mit["f_roh"] == 30 and mit["f_soc"] == 30
    ref = reserve_referenz(zeit(tag, 21, 0), tage_aus_szenario(zeit(tag, 21, 0), (40, 40, 40), forecast=fc, a=0.0),
                           [200] * 48, kap_kwh=mit["batterie_kapazitaet"])
    assert mit["reserve_plan"]["kwh"] == pytest.approx(ref["kwh"], abs=0.001) and mit["reserve_pct"] == pytest.approx(ref["pct"], abs=0.01)
    assert mit["reserve_plan"]["bis"] == ref["bis"].isoformat()
    plan = plan_referenz(zeit(tag, 21, 0), tage_aus_szenario(zeit(tag, 21, 0), (40, 40, 40), forecast=fc), [0.19] * 48,
                         kap_kwh=mit["batterie_kapazitaet"], notstrom_pct=ref["pct"], soc=85.0)
    assert plan["f_soc"] == 30

    fuenf = szenario(blueprint, zeit(tag, 21, 0), zustands_overrides=_notstrom_zustaende([200] * 48, shutdown=5), **basis).auswerten(bis="f_soc")
    assert fuenf["shutdown_soc"] == 5 and fuenf["reserve_pct"] == pytest.approx(22.648, abs=0.01) and fuenf["f_soc"] == 25


def test_notstromreserve_ueberschuss_dazwischen_mindert_das_spaetere_defizit(blueprint, tag):
    """
    Wie oben (Last 0,258 kWh je Slot nach Verlusten), aber Tag 3 dunkel (P10 2 kWh = 0,1 kWh je Slot):
    Mit 40 kWh morgen ueberwiegt der Ueberschuss (20 x (1,84 - 0,258) = 31,6 kWh) alles Spaetere, das Maximum
    bleibt die erste Nacht. Mit 14 kWh morgen (P10 0,7 kWh je Slot x 0,92 = 0,644) bleibt der Ueberschuss bei
    20 x 0,386 = 7,72 kWh; zweite Nacht 28 x 0,258 = 7,22, Tag 3 20 x (0,258 - 0,092) = 3,32, Abend 6 x 0,258 = 1,55:
    5,674 - 7,72 + 7,22 + 3,32 + 1,55 = 10,04 kWh, Maximum am Horizontende (Tag 3, 21:00).
    Last 700 Wh je Slot (0,784 kWh) liegt ueber jedem P10-Slot: 96 x 0,784 - 20 x 0,644 - 20 x 0,092 = 60,56 kWh.
    """
    fc = prognose_gleichmaessig(tag, 2.0)
    j = zeit(tag, 21, 0)
    faelle = {"hell": ((40, 2, 40), 200, 5.674, 1, "08:00"),
              "dunkel": ((14, 2, 40), 200, 5.674 - 20 * (0.7 * 0.92 - 0.245 / 0.95) + 28 * 0.245 / 0.95 + 20 * (0.245 / 0.95 - 0.1 * 0.92) + 6 * 0.245 / 0.95, 2, "21:00"),
              "schwer": ((14, 2, 40), 700, 96 * 0.745 / 0.95 - 20 * 0.7 * 0.92 - 20 * 0.1 * 0.92, 2, "21:00")}
    for name, (prog, wh, kwh, tage, uhr) in faelle.items():
        ctx = szenario(blueprint, j, forecast=fc, prognose_tage_kwh=prog, soc=85.0,
                       zustands_overrides=_notstrom_zustaende([wh] * 48)).auswerten(bis="halten_fall")
        assert ctx["reserve_plan"]["kwh"] == pytest.approx(kwh, abs=0.01), name
        assert ctx["reserve_plan"]["bis"].endswith(f"{(tag + dt.timedelta(days=tage)).isoformat()}T{uhr}:00+02:00"), name
        ref = reserve_referenz(j, tage_aus_szenario(j, prog, forecast=fc, a=0.0), [wh] * 48, kap_kwh=ctx["batterie_kapazitaet"])
        assert ctx["reserve_plan"]["kwh"] == pytest.approx(ref["kwh"], abs=0.001) and ctx["reserve_plan"]["bis"] == ref["bis"].isoformat(), name
    # Reserve ueber dem Ladestand: halten am Ladestand, wie bei der Planung
    assert ctx["f_roh"] > 85 and ctx["halten_fall"] is True and ctx["f_soc"] == 85


def test_notstromreserve_am_tag_ohne_defizit(blueprint, tag):
    """12:00, heller Tag: PV10 0,7 kWh x 0,92 = 0,644 > 0,258 je Slot bis 18:00, Ueberschuss 12 x 0,386 = 4,63 kWh;
    die Nacht (28 Slots x 0,258 = 7,22 kWh) uebersteigt ihn: Reserve 2,59 kWh bis 08:00. Mit 40 kWh P10 heute
    (2,0 kWh je Slot) bleibt die Summe negativ: Reserve 0, bis leer, Untergrenze = Abschalt-Ladestand."""
    fc = prognose_gleichmaessig(tag, 2.0)
    zs = _notstrom_zustaende([200] * 48)
    ctx = szenario(blueprint, zeit(tag, 12, 0), forecast=fc, prognose_tage_kwh=(40, 40, 40), soc=70.0, zustands_overrides=zs).auswerten(bis="f_soc")
    assert ctx["reserve_plan"]["kwh"] == pytest.approx(28 * 0.245 / 0.95 - 12 * (0.7 * 0.92 - 0.245 / 0.95), abs=0.001)
    hell = prognose_gleichmaessig(tag, 2.0 / 0.7)  # P10 = 2,0 kW
    ctx2 = szenario(blueprint, zeit(tag, 12, 0), forecast=hell, prognose_tage_kwh=(40, 40, 40), soc=70.0, zustands_overrides=zs).auswerten(bis="f_soc")
    assert ctx2["reserve_plan"]["kwh"] == 0 and ctx2["reserve_plan"]["bis"] is None and ctx2["reserve_pct"] == 10 and ctx2["reserve_5"] == 10


def test_notstromprofil_nur_mit_48_werten(blueprint, tag):
    from conftest import fake_entity
    helfer = fake_entity("notstrom_json_text", "input_text")
    for wert in ("", "unknown", json.dumps([50] * 47), "[]"):
        ctx = szenario(blueprint, zeit(tag, 21, 0), zustands_overrides={helfer: Zustand(helfer, wert)}).auswerten(bis="reserve_pct")
        assert ctx["notstrom_profil"] is None and ctx["reserve_pct"] == 0, wert
    ohne = szenario(blueprint, zeit(tag, 21, 0), input_overrides={"notstrom_json_text": ""}).auswerten(bis="reserve_pct")
    assert ohne["notstrom_profil"] is None and ohne["reserve_pct"] == 0


def test_temperaturprofil_lernt_in_zehntelgrad(blueprint, tag):
    from conftest import fake_entity
    t = fake_entity("aussentemperatur_sensor", "sensor"); helfer = fake_entity("temperatur_json_text", "input_text")
    # Leerer Helfer: Erstbelegung mit dem Messwert in allen 48 Slots
    h = szenario(blueprint, zeit(tag, 14, 0), zustands_overrides={t: Zustand(t, "12.0")}, trigger_id="update_json")
    ctx = _update_json_zweig(blueprint, h, zeit(tag, 14, 0))
    assert ctx["tp_mess"] == 12.0 and ctx["tp_mittel"] is None
    neu = ctx["tp_neu"] if isinstance(ctx["tp_neu"], list) else json.loads(ctx["tp_neu"])
    assert neu == [120] * 48
    # Bestehendes Profil 10.0 Grad, Messung 12.0 -> Slot 13:30-14:00 (Index 27): 100 x 6/7 + 120 / 7 = 102.9 -> 103
    h2 = szenario(blueprint, zeit(tag, 14, 0), zustands_overrides={t: Zustand(t, "12.0"), helfer: Zustand(helfer, json.dumps([100] * 48))}, trigger_id="update_json")
    ctx2 = _update_json_zweig(blueprint, h2, zeit(tag, 14, 0))
    neu2 = ctx2["tp_neu"] if isinstance(ctx2["tp_neu"], list) else json.loads(ctx2["tp_neu"])
    assert neu2[27] == 103 and neu2[26] == 100 and len(json.dumps(neu2, separators=(",", ":"))) <= 255
    assert ctx2["tp_mittel"] == 10.0
    # Kaeltester Fall passt in den Helfer: 48 x "-123," = 240 Zeichen
    assert len(json.dumps([-123] * 48, separators=(",", ":"))) <= 255
    # Ohne Sensor: kein Wert, kein Schreiben
    h3 = szenario(blueprint, zeit(tag, 14, 0), input_overrides={"aussentemperatur_sensor": ""}, trigger_id="update_json")
    ctx3 = _update_json_zweig(blueprint, h3, zeit(tag, 14, 0))
    assert ctx3["tp_mess"] is None and "tp_neu" not in ctx3


def test_wetter_zeile_haelt_24_stunden_je_quelle_fest(blueprint, tag):
    import datetime as _dt
    from conftest import fake_entity
    t = fake_entity("aussentemperatur_sensor", "sensor")
    q1, q2 = "weather.open_meteo", "weather.dwd"
    jetzt = zeit(tag, 14, 0)
    h = szenario(blueprint, jetzt, zustands_overrides={t: Zustand(t, "12.0")}, input_overrides={"wetter_prognose_entities": [q1, q2]}, trigger_id="update_json")
    ctx = _update_json_zweig(blueprint, h, jetzt)
    assert ctx["wetter_liste"] == [q1, q2]
    # Stundenprognose ab 12:00 (zwei Stunden alt) bis uebermorgen; nur q1 hat geantwortet
    start = jetzt.replace(minute=0) - _dt.timedelta(hours=2)
    fc = [{"datetime": (start + _dt.timedelta(hours=i)).isoformat(), "temperature": 10.0 + i * 0.5} for i in range(48)]
    ctx["wetter_antwort"] = {q1: {"forecast": fc}}
    block = next(st for st in blueprint["action"] if isinstance(st, dict) and "if" in st and "update_json" in str(st["if"]))
    def finde(schritte):
        for st in schritte:
            if st.get("action") == "notify.send_message" and "'wetter'" in st["data"]["message"]:
                return st["data"]["message"]
            if "then" in st:
                r = finde(st["then"])
                if r: return r
    zeile = json.loads(h.render(finde(block["then"]), ctx))
    assert zeile["art"] == "wetter" and zeile["aussen_temp"] == 12.0 and zeile["kennung"] == ctx["lauf_kennung"]
    # Erster Wert ist die laufende Stunde 14:00 (Index 2 -> 11.0 Grad), dann 24 Stunden
    assert zeile["quellen"][q1]["von"].startswith(f"{tag.isoformat()}T14:00") and len(zeile["quellen"][q1]["temp"]) == 24
    assert zeile["quellen"][q1]["temp"][0] == 11.0 and zeile["quellen"][q1]["temp"][-1] == pytest.approx(22.5)
    assert zeile["quellen"][q2] == {"von": None, "temp": []}
