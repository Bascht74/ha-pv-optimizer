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

from conftest import prognose_gleichmaessig, szenario, zeit
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
    ("Sommer: 40/40/40 kWh, alles wuerde einspeisen -> Minimum", (40, 40, 40), 166.7, 20),
    ("Herbst: 15/5/5 kWh, morgen +4,68 kWh = 14,6 % -> 90 - 14,6 = 75,4 -> 75", (15, 5, 5), 14.56, 75),
    ("Dezember: 2/2/2 kWh, keine Auffuellung -> halten beim Ladestand 85", (2, 2, 2), 0.0, 85),
    ("Strecke: 3/30/30 kWh, Sonnentage ab uebermorgen geben die Nacht davor frei: 63,5 -> 60", (3, 30, 30), 26.5, 60),
])
def test_entlade_untergrenze(blueprint, tag, lage, prognose, b_stern, f_soc):
    ctx = szenario(blueprint, zeit(tag, 21, 0), soc=85.0, prognose_tage_kwh=prognose).auswerten(bis="tou_schreiben")
    assert ctx["entlade_aktiv"] is True
    assert ctx["b_stern_pct"] == pytest.approx(b_stern, abs=0.1), lage
    assert ctx["f_soc"] == f_soc, lage


@pytest.mark.parametrize("soc, tou, erwartet", [
    (95.0, 20, False),   # weit ueber der Grenze: Register bleibt, bis es wirken kann
    (78.0, 20, False),   # 78 > 75 + 2: noch ein Lauf zu frueh
    (77.0, 20, True),    # 77 <= 75 + 2: jetzt schreiben, die Nacht laeuft auf die Grenze zu
    (77.0, 72, False),   # Aenderung 75 - 72 = 3 < 5 Punkte
])
def test_untergrenze_wird_nur_beim_annaehern_geschrieben(blueprint, tag, soc, tou, erwartet):
    h = szenario(blueprint, zeit(tag, 21, 0), soc=soc, prognose_tage_kwh=(15, 5, 5), zustands_overrides=_tou(tou))
    ctx = h.auswerten(bis="tou_schreiben")
    assert ctx["f_soc"] == 75
    assert ctx["tou_schreiben"] is erwartet


def test_untergrenze_liegt_auf_dem_5er_raster(blueprint, tag):
    """Strecke: Ziel-Kandidat 63,5 -> abgerundet 60, nicht 63; Halten bleibt am Ladestand (83), nicht 80."""
    ctx = szenario(blueprint, zeit(tag, 21, 0), soc=85.0, prognose_tage_kwh=(3, 30, 30)).auswerten(bis="f_soc")
    assert ctx["f_roh"] == pytest.approx(63.5, abs=0.1)
    assert ctx["f_soc"] == 60
    ctx = szenario(blueprint, zeit(tag, 21, 0), soc=83.0, prognose_tage_kwh=(2, 2, 2)).auswerten(bis="f_soc")
    assert ctx["f_roh"] == pytest.approx(90.0)
    assert ctx["f_soc"] == 83


def test_halten_wird_sofort_geschrieben(blueprint, tag):
    """Dezember: Untergrenze 90 liegt ueber dem Ladestand 85 -> Grenze = 85, Ladestand steht per Definition daran."""
    ctx = szenario(blueprint, zeit(tag, 21, 0), soc=85.0, prognose_tage_kwh=(2, 2, 2), zustands_overrides=_tou(20)).auswerten(bis="tou_schreiben")
    assert ctx["f_roh"] == pytest.approx(90.0)
    assert ctx["f_soc"] == 85
    assert ctx["tou_schreiben"] is True


def test_ohne_prognose_morgen_bleibt_die_planung_aus(blueprint, tag):
    ctx = szenario(blueprint, zeit(tag, 21, 0), soc=60.0, prognose_tage_kwh=None, zustands_overrides=_tou(20)).auswerten(bis="tou_schreiben")
    assert ctx["entlade_aktiv"] is False
    assert ctx["prognose_tage"] == []
    assert ctx["tou_schreiben"] is False


def test_zellausgleich_faellig_hebt_das_ziel_auf_100(blueprint, tag):
    """Herbst-Lage, Batterie seit 12 Tagen nicht voll: Ziel 100 % -> Untergrenze 100 - 14,6 = 85."""
    from conftest import fake_entity
    eid = fake_entity("json_tracking_sensor", "input_text")
    liste = json.dumps([(tag - dt.timedelta(days=d)).isoformat() for d in (12, 13, 14)])
    ctx = szenario(blueprint, zeit(tag, 21, 0), soc=85.0, prognose_tage_kwh=(15, 5, 5),
                   zustands_overrides={eid: Zustand(eid, liste)}).auswerten(bis="tou_schreiben")
    assert ctx["tage_seit_voll"] == 12
    assert ctx["zellausgleich_faellig"] is True
    assert ctx["ziel_soc_eff"] == 100
    assert ctx["f_soc"] == 85


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


def test_horizont_beginnt_vor_sonnenaufgang_mit_heute(blueprint, tag):
    """
    Nach Mitternacht fuellt HEUTE die Batterie wieder auf, dunkle Folgetage (2/2/2) aendern daran nichts.
    Von Hand: heute 17.25 kWh x 0.92 - 9.12 kWh = 6.75 kWh = 21.0 % von 32.15 kWh -> Ziel-Kandidat 69 -> 65.
    Abends zaehlt nur noch morgen (dunkel) -> halten beim Ladestand.
    """
    nacht = szenario(blueprint, zeit(tag, 3, 0), soc=85.0, prognose_tage_kwh=(2, 2, 2)).auswerten(bis="f_soc")
    tage = nacht["prognose_tage"]
    assert [t["name"] for t in tage] == ["heute", "morgen", "Tag 3"]
    assert [t["vertrauen"] for t in tage] == [1.0, 1.0, 0.7]
    assert tage[0]["pv"] == pytest.approx(17.25, abs=0.05) and tage[1]["pv"] == pytest.approx(2.0)
    assert nacht["b_stern_pct"] == pytest.approx(21.0, abs=0.2)
    assert nacht["f_soc"] == 65
    abend = szenario(blueprint, zeit(tag, 21, 0), soc=85.0, prognose_tage_kwh=(2, 2, 2)).auswerten(bis="f_soc")
    assert [t["name"] for t in abend["prognose_tage"]] == ["morgen", "Tag 3", "Tag 4"]
    assert abend["f_soc"] == 85


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
    ctx = {"trigger": {"id": "update_json", "now": trigger_zeit}}
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
    assert ctx["hb_haelt"] is (erwartet is not None)
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
