"""
Rechnungstests - die variables:-Kette wird mit dem Harness ausgewertet und
gegen von Hand berechnete Erwartungswerte geprueft.

Jeder Test hier ist einer der Defekte, die aus Logbuch-Analysen kamen, oder
eine Invariante, deren Verletzung einen solchen Defekt bedeuten wuerde.
Bei einem roten Test steht im Namen, welche Regel gerissen ist.
"""
from __future__ import annotations

import datetime as dt

import pytest

from conftest import prognose_gleichmaessig, szenario, zeit

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
        from ha_jinja import Zustand
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
# Hausverbrauchs-Hinweis in Prio 8 und Fall B
# --------------------------------------------------------------------------
@pytest.mark.parametrize("minute, slot_kwh, meldet", [
    (25, 0.80, True),   # Backofen: 0,80 kWh nach 25 min gegen 0,19 x 25/30 = 0,16 erwartet
    (25, 0.13, False),  # ruhiges Haus
    (25, 0.27, False),  # Blip: 1,7x, aber nur 0,11 kWh ueber Profil
    (5, 0.20, False),   # zu frueh im Slot - Rauschen
])
def test_haus_log_addon_schwellen(blueprint, tag, minute, slot_kwh, meldet):
    h = szenario(blueprint, zeit(tag, 17, minute), hausverbrauch_slot_kwh=slot_kwh)
    text = h.auswerten(bis="haus_log_addon")["haus_log_addon"]
    assert (text != "") is meldet, repr(text)
    if meldet:
        assert "Hausverbrauch im laufenden Zeitfenster" in text


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
    zwei Packs, und freie_kwh geht in Blockade, Fall B und Prio 8 ein.
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
    falschen Slot des EMA-Profils, an dem Blockade und Prio 8 haengen.
    """
    trigger_zeit = zeit(tag, trigger_hh, trigger_mm)
    h = szenario(blueprint, trigger_zeit + dt.timedelta(minutes=verzug_min), trigger_id="update_json")
    vars_ = _update_json_variablen(blueprint)
    ctx = {"trigger": {"id": "update_json", "now": trigger_zeit}}
    ctx.update(h._aufloesen({"var_hausverbrauch_json": vars_["var_hausverbrauch_json"]}, ctx))
    assert h._aufloesen(vars_["h_index"], ctx) == erwartet
    ist_mitternacht = h._aufloesen(vars_["ist_mitternachtslauf"], ctx)
    assert ist_mitternacht is (trigger_hh == 0), f"ist_mitternachtslauf={ist_mitternacht!r}"
