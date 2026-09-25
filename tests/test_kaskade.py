"""
Die choose:-Gruppen: welcher Zweig gewinnt, und was er schreibt.

Die uebrigen Tests pruefen die Zahlen, die in die Entscheidung eingehen. Hier
geht es um die Entscheidung selbst - die Reihenfolge der Zweige und ihre
Kopplungen. Erfasst sind alle Gruppen auf oberster Ebene, nicht nur die
Prioritaetskaskade: test_jeder_zweig_ist_abgedeckt haelt die Abdeckung
vollstaendig, kommt ein Zweig dazu, wird der Test rot, bis ein Szenario ihn
erreicht.

Die Abdeckung prueft nur die Auswahl (gewinner), nicht die Sequenz: Drei Zweige
benutzen repeat:/delay:, die der Ausfuehrer bewusst verweigert. Was ein Zweig
schreibt, steht darunter je Zweig - dort, wo der Schreibvorgang etwas aussagt.
"""
from __future__ import annotations

import datetime as dt

import pytest

from conftest import fake_entity, standard_inputs, szenario, zeit
from ha_jinja import Zustand
from kaskade import (gewinner, gruppe, gruppen, schreibt, template_wahr, zweig,
                     zweig_und_aktionen)


def _z(blueprint: dict, name: str, wert: str, **kw) -> dict:
    """
    Ein Zustands-Override fuer eine der Fake-Entitaeten.

    Die Domain kommt aus dem Blueprint, nicht aus dem Namen: Eine falsch
    geratene Domain ergibt eine andere Entitaets-ID, der Override liefe ins
    Leere und das Szenario pruefte still etwas anderes als es behauptet.
    """
    eid = standard_inputs(blueprint)[name]
    return {eid: Zustand(eid, wert, **kw)}


# --------------------------------------------------------------------------
# Je Zweig ein Szenario. Der Kommentar nennt die Bedingung, die ihn gewinnen
# laesst - nicht die Zahlen, die dabei herauskommen.
# --------------------------------------------------------------------------
def p0_temperaturdeckel(blueprint, tag):
    """Kalte Zellen senken die zulaessige Laderate unter den Sollwert."""
    ov = {**_z(blueprint, "bms1_temp_min_sensor", "12.0"), **_z(blueprint, "bms2_temp_min_sensor", "12.0")}
    return szenario(blueprint, zeit(tag, 12, 0), ladestrom=200.0, zustands_overrides=ov)


def p1_zellausgleich(blueprint, tag):
    """Eine Zellspannung erreicht die Balancing-Schwelle, die Batterie war heute noch nicht voll."""
    return szenario(blueprint, zeit(tag, 12, 0), soc=98.0,
                    zustands_overrides=_z(blueprint, "vmax1_sensor", "3.45"))


def p2_fall_b(blueprint, tag):
    """Niedriger Ladestand: die Restzeit reicht nicht, der Strom wird aufgerissen."""
    return szenario(blueprint, zeit(tag, 12, 0), soc=40.0)


def p3_wp_anlaufsperre(blueprint, tag):
    """Der Anlauf-Timer der Waermepumpe laeuft, der Ladestrom wird gehalten."""
    return szenario(blueprint, zeit(tag, 12, 0), input_overrides={"wp_boost_aktiv": True},
                    zustands_overrides=_z(blueprint, "helper_timer_wp_anlauf", "active"))


def p4_wp_kompressor(blueprint, tag):
    """Der Verdichter laeuft, der Ueberschuss gehoert ihm."""
    return szenario(blueprint, zeit(tag, 12, 0),
                    zustands_overrides=_z(blueprint, "wp_kompressor_sensor", "on"))


def p5_peak_shaving(blueprint, tag):
    """Der Kappungs-Timer laeuft und die Einspeisung liegt ueber der Schwelle."""
    ov = {**_z(blueprint, "helper_timer_peak", "active"), **_z(blueprint, "grid_export_sensor", "-8000")}
    return szenario(blueprint, zeit(tag, 12, 0), soc=84.0, zustands_overrides=ov)


def p6_morgen_blockade(blueprint, tag):
    """Frueh am Tag, Platz fuer eine erwartete Einspeisespitze halten."""
    return szenario(blueprint, zeit(tag, 7, 0), soc=84.0, input_overrides={"schwelle_peak_shaving": 3500})


def p7_dynamisch(blueprint, tag):
    """Normalbetrieb: gedrosselt so laden, dass die Batterie spaet voll wird."""
    return szenario(blueprint, zeit(tag, 12, 0))


def p8_notbetrieb(blueprint, tag):
    """Der Ladestand des Wechselrichters fehlt seit einer Stunde."""
    return szenario(blueprint, zeit(tag, 12, 0), soc="unavailable", ladestrom=50.0)


ZWEIGE = [
    ("PRIO 0", p0_temperaturdeckel),
    ("PRIO 1", p1_zellausgleich),
    ("PRIO 2", p2_fall_b),
    ("PRIO 3", p3_wp_anlaufsperre),
    ("PRIO 4", p4_wp_kompressor),
    ("PRIO 5", p5_peak_shaving),
    ("PRIO 6", p6_morgen_blockade),
    ("PRIO 7", p7_dynamisch),
    ("PRIO 8", p8_notbetrieb),
]


# --------------------------------------------------------------------------
# Die Gruppen neben der Kaskade. Sie konkurrieren nicht um den Ladestrom,
# sondern haengen an einem eigenen Trigger oder an einer eigenen Lage.
# --------------------------------------------------------------------------
def mitternacht(blueprint, tag):
    """Der Wartungslauf um 23:58; die drei Mitternachts-Gruppen haengen nur am Trigger."""
    return szenario(blueprint, zeit(tag, 23, 58), trigger_id="maintenance")


def tou_minimum(blueprint, tag):
    """Wartungslauf ohne Entlade-Planung, die Register stehen noch auf einem Nachtwert."""
    ov = {}
    for i in range(1, 7):
        ov.update(_z(blueprint, f"wr_tou_{i}", "55"))
    return szenario(blueprint, zeit(tag, 23, 58), trigger_id="maintenance",
                    prognose_tage_kwh=None, zustands_overrides=ov)


def sonnenuntergang(blueprint, tag):
    """Der Sonnenuntergangs-Lauf parkt den Verlust-Kandidaten des Tages."""
    return szenario(blueprint, zeit(tag, 21, 30), trigger_id="sunset_check")


def verlust_verbuchen(blueprint, tag):
    """Nachts unter die Untergrenze gefallen, ein Kandidat vom Vorabend steht offen."""
    return szenario(blueprint, zeit(tag, 3, 0), soc=19.0,
                    zustands_overrides=_z(blueprint, "helper_offener_verlust", "2.0"))


def peak_shaving_ende(blueprint, tag):
    """Der Kappungs-Timer ist von active auf idle gefallen."""
    return szenario(blueprint, zeit(tag, 12, 0), trigger_id="peak_shaving_ende")


def peak_timer_aufziehen(blueprint, tag):
    """
    Einspeisung ueber der Kappungs-Schwelle. Das Warmwasser ist warm, sonst
    nimmt der Waermepumpen-Boost den Ueberschuss und der Timer bliebe aus.
    """
    ov = {**_z(blueprint, "grid_export_sensor", "-8000"), **_z(blueprint, "wp_temp_sensor", "58.0")}
    return szenario(blueprint, zeit(tag, 12, 0), soc=84.0, zustands_overrides=ov)


def peak_timer_loeschen(blueprint, tag):
    """Die Anlaufsperre der Waermepumpe greift, der Kappungs-Timer wird abgebrochen."""
    return szenario(blueprint, zeit(tag, 12, 0), soc=84.0, input_overrides={"wp_boost_aktiv": True},
                    zustands_overrides=_z(blueprint, "helper_timer_wp_anlauf", "active"))


def wp_boost_start(blueprint, tag):
    """Einspeisung ueber der Boost-Schwelle, das Warmwasser liegt unter dem Startwert."""
    ov = {**_z(blueprint, "grid_export_sensor", "-8000"), **_z(blueprint, "wp_temp_sensor", "30.0")}
    return szenario(blueprint, zeit(tag, 12, 0), zustands_overrides=ov)


def wp_boost_ende(blueprint, tag):
    """Der Laufzeit-Timer ist abgelaufen, das Ziel steht noch auf dem PV-Wert."""
    return szenario(blueprint, zeit(tag, 12, 0), trigger_id="wp_boost_ende",
                    zustands_overrides=_z(blueprint, "wp_water_heater", "heat", attributes={"temperature": 59}))


def wp_boost_watchdog(blueprint, tag):
    """Kein Timer laeuft mehr, das Ziel steht trotzdem noch auf dem PV-Wert."""
    return szenario(blueprint, zeit(tag, 12, 0),
                    zustands_overrides=_z(blueprint, "wp_water_heater", "heat", attributes={"temperature": 59}))


WEITERE_ZWEIGE = [
    ("100%-Tage-Tracking", mitternacht),
    ("Tageshelfer zurücksetzen", mitternacht),
    ("Tägliches Aufräumen", mitternacht),
    ("ToU auf Minimum", tou_minimum),
    ("Aktionen bei Sonnenuntergang", sonnenuntergang),
    ("Speicherverlust verbuchen", verlust_verbuchen),
    ("PEAK-SHAVING ENDE", peak_shaving_ende),
    ("Zieht den Peak-Shaving Timer auf", peak_timer_aufziehen),
    ("Löscht den Peak-Shaving Timer", peak_timer_loeschen),
    ("WP-BOOST: START", wp_boost_start),
    ("WP-BOOST: ENDE", wp_boost_ende),
    ("WP-BOOST: WATCHDOG", wp_boost_watchdog),
]

ALLE_ZWEIGE = ZWEIGE + WEITERE_ZWEIGE


@pytest.mark.parametrize("quelle, erwartet", [
    ("{{ true }}", True), ("{{ false }}", False),
    ("{{ 'true' }}", True), ("{{ 'True' }}", True),
    ("{{ 'false' }}", False), ("{{ 'an' }}", False), ("{{ 1 }}", False), ("{{ '' }}", False),
])
def test_template_bedingung_wie_home_assistant(blueprint, tag, quelle, erwartet):
    """
    Home Assistant haelt eine Template-Bedingung nur fuer erfuellt, wenn ihr
    gerenderter TEXT 'true' ist. Python-Wahrheit ist der falsche Massstab: Jeder
    nicht leere Text waere damit ein Ja - auch das Wort 'false'.
    """
    h = szenario(blueprint, zeit(tag, 12, 0))
    assert template_wahr(h, quelle, h.auswerten()) is erwartet


@pytest.mark.parametrize("prio, bauen", ZWEIGE, ids=[p for p, _ in ZWEIGE])
def test_zweig_gewinnt_in_seinem_szenario(blueprint, tag, prio, bauen):
    assert zweig(bauen(blueprint, tag), blueprint) == prio


@pytest.mark.parametrize("alias_anfang, bauen", WEITERE_ZWEIGE, ids=[a for a, _ in WEITERE_ZWEIGE])
def test_zweig_der_weiteren_gruppen_gewinnt(blueprint, tag, alias_anfang, bauen):
    """Nur die Auswahl: drei dieser Zweige spielt der Ausfuehrer nicht (repeat:/delay:)."""
    z = gewinner(bauen(blueprint, tag), gruppe(blueprint, alias_anfang))
    assert z is not None and z["alias"].startswith(alias_anfang), \
        f"getroffen: {z['alias'] if z else None}"


def test_jeder_zweig_ist_abgedeckt(blueprint, tag):
    """
    Die Szenarien oben erreichen JEDEN Zweig JEDER choose:-Gruppe auf oberster
    Ebene. Kommt einer dazu - auch in einer neuen Gruppe - oder wird einer so
    eng, dass ihn kein Szenario mehr trifft, wird dieser Test rot.
    """
    erwartet = {z["alias"] for g in gruppen(blueprint) for z in g}
    getroffen = set()
    for alias_anfang, bauen in ALLE_ZWEIGE:
        z = gewinner(bauen(blueprint, tag), gruppe(blueprint, alias_anfang))
        if z is not None:
            getroffen.add(z["alias"])
    assert getroffen == erwartet, f"nicht abgedeckt: {sorted(erwartet - getroffen)}"


# --------------------------------------------------------------------------
# Was der gewinnende Zweig an den Wechselrichter schreibt
# --------------------------------------------------------------------------
def test_temperaturdeckel_schreibt_die_laderate(blueprint, tag):
    """Prio 0 setzt genau den Temperaturdeckel, nicht den Sollwert des Zweigs darunter."""
    h = p0_temperaturdeckel(blueprint, tag)
    ctx = h.auswerten()
    alias, aktionen = zweig_und_aktionen(h, blueprint, ctx)
    assert alias.startswith("PRIO 0")
    assert schreibt(aktionen) == [ctx["temperatur_limit_ampere"]]
    assert ctx["temperatur_limit_ampere"] < ctx["max_ampere"]


def test_zellausgleich_setzt_strom_timer_und_modus(blueprint, tag):
    """
    Prio 1 schreibt den Ausgleichs-Ladestrom, markiert die Batterie als voll und
    startet den Nachlauf-Timer. Die drei gehoeren zusammen: Ohne den Timer endet
    der Ausgleich nie, ohne die Markierung liefe er beim naechsten Lauf erneut an.
    """
    h = p1_zellausgleich(blueprint, tag)
    ctx = h.auswerten()
    alias, aktionen = zweig_und_aktionen(h, blueprint, ctx)
    assert alias.startswith("PRIO 1")
    assert schreibt(aktionen) == [ctx["target_p1"]]
    assert ctx["target_p1"] < ctx["max_ampere"], "Ausgleich laedt bewusst klein"
    assert schreibt(aktionen, "input_select.select_option") == ["top_balancing"]
    dienste = [s for s, _, _ in aktionen]
    assert "timer.start" in dienste and "input_boolean.turn_on" in dienste


def test_fall_b_reisst_auf_das_maximum_auf(blueprint, tag):
    h = p2_fall_b(blueprint, tag)
    ctx = h.auswerten()
    _, aktionen = zweig_und_aktionen(h, blueprint, ctx)
    assert schreibt(aktionen) == [ctx["max_ampere"]]
    assert "fall_b_max" in schreibt(aktionen, "input_select.select_option")


def test_anlaufsperre_haelt_den_strom_statt_ihn_zu_schreiben(blueprint, tag):
    """Prio 3 haelt bewusst: kein Schreibvorgang, nur der Modus."""
    _, aktionen = zweig_und_aktionen(p3_wp_anlaufsperre(blueprint, tag), blueprint)
    assert schreibt(aktionen) == []
    assert "wp_sperre" in schreibt(aktionen, "input_select.select_option")


def test_kompressor_sperre_stoppt_das_laden(blueprint, tag):
    _, aktionen = zweig_und_aktionen(p4_wp_kompressor(blueprint, tag), blueprint)
    assert schreibt(aktionen) == [0]


def test_ohne_kompressorsensor_sperrt_prio_4_nicht(blueprint, tag):
    """
    Unbelegtes Feld - der Regelfall an beiden Standorten. Traegt die Groesse ihr
    Nein als Text statt als Boolean, gewinnt Prio 4 jeden Lauf und kein Zweig
    darunter ist mehr zu sehen.
    """
    h = szenario(blueprint, zeit(tag, 12, 0), input_overrides={"wp_kompressor_sensor": ""})
    assert h.auswerten()["wp_kompressor_aktiv"] is False
    assert zweig(h, blueprint) == "PRIO 7"


def test_blockade_stoppt_das_laden(blueprint, tag):
    _, aktionen = zweig_und_aktionen(p6_morgen_blockade(blueprint, tag), blueprint)
    assert schreibt(aktionen) == [0]
    assert "blockade" in schreibt(aktionen, "input_select.select_option")


def test_peak_shaving_hebt_den_strom_an(blueprint, tag):
    h = p5_peak_shaving(blueprint, tag)
    ctx = h.auswerten()
    _, aktionen = zweig_und_aktionen(h, blueprint, ctx)
    assert schreibt(aktionen) == [ctx["target_p3"]]
    assert ctx["target_p3"] > ctx["aktueller_ladestrom"]


def test_dynamisches_laden_schreibt_den_gedrosselten_sollwert(blueprint, tag):
    h = p7_dynamisch(blueprint, tag)
    ctx = h.auswerten()
    _, aktionen = zweig_und_aktionen(h, blueprint, ctx)
    assert schreibt(aktionen) == [ctx["target_p5"]]
    assert ctx["target_p5"] < ctx["max_ampere"]


# --------------------------------------------------------------------------
# Vorrang: die Kopplungen, an denen eine Aenderung an einem Zweig einen
# anderen still aushebeln kann (Regel 9)
# --------------------------------------------------------------------------
def test_waermepumpe_hat_vorrang_vor_der_kappung(blueprint, tag):
    """Laufen beide Timer, haelt die Anlaufsperre - sonst nimmt die Kappung der Pumpe den Ueberschuss."""
    ov = {**_z(blueprint, "helper_timer_peak", "active"), **_z(blueprint, "helper_timer_wp_anlauf", "active"),
          **_z(blueprint, "grid_export_sensor", "-8000")}
    h = szenario(blueprint, zeit(tag, 12, 0), soc=84.0, input_overrides={"wp_boost_aktiv": True},
                 zustands_overrides=ov)
    assert zweig(h, blueprint) == "PRIO 3"


def test_volle_batterie_laesst_kappung_und_nachladen_aus(blueprint, tag):
    """Ueber 99 % nimmt die Batterie nichts mehr auf: kein Zweig greift."""
    ov = {**_z(blueprint, "helper_timer_peak", "active"), **_z(blueprint, "helper_batterie_heute_voll", "on"),
          **_z(blueprint, "grid_export_sensor", "-8000")}
    assert zweig(szenario(blueprint, zeit(tag, 12, 0), soc=99.5, zustands_overrides=ov), blueprint) is None


def test_nachts_greift_kein_zweig(blueprint, tag):
    assert zweig(szenario(blueprint, zeit(tag, 23, 0), soc=84.0), blueprint) is None


def test_fall_b_legt_das_dynamische_laden_still(blueprint, tag):
    """Steht der Modus schon auf Fall B, gewinnt Prio 7 zwar, stoppt aber ohne zu schreiben."""
    eid = fake_entity("helper_lade_modus", "input_select")
    h = szenario(blueprint, zeit(tag, 12, 0), soc=93.0,
                 zustands_overrides={eid: Zustand(eid, "fall_b_max")})
    alias, aktionen = zweig_und_aktionen(h, blueprint)
    assert alias.startswith("PRIO 7")
    assert schreibt(aktionen) == []
    assert any(s == "stop" for s, _, _ in aktionen)


def test_kappung_wartet_den_mindestabstand_ab(blueprint, tag):
    """Wurde das Register gerade geschrieben, gewinnt Prio 5, schreibt aber nicht."""
    eid = fake_entity("wr_max_charge_current", "number")
    ov = {**_z(blueprint, "helper_timer_peak", "active"), **_z(blueprint, "grid_export_sensor", "-8000"),
          eid: Zustand(eid, "200.0", last_changed=zeit(tag, 12, 0) - dt.timedelta(seconds=10))}
    h = szenario(blueprint, zeit(tag, 12, 0), soc=84.0, zustands_overrides=ov)
    alias, aktionen = zweig_und_aktionen(h, blueprint)
    assert alias.startswith("PRIO 5")
    assert schreibt(aktionen) == []


# --------------------------------------------------------------------------
# Ladestrom in Zahlen: welcher Wert geschrieben wird und wann. Die Zielstroeme
# sind von Hand gerechnet wie in test_rechnung (1 A je Halbstunde = 0,0256 kWh).
# --------------------------------------------------------------------------
# 10:00, 4 kW ab 08:00, 14 Halbstunden bis 1 h vor Fensterende: freie Kapazitaet
# / 0,3584 kWh je A, aufgerundet, + 2 A. 80 %: 6,43 -> 18 + 2; 89,9 %: 3,25 -> 10 + 2;
# 90 %: 3,22 -> 9 + 2; 92 %: 2,57 -> 8 + 2.
ZIELSTROM_10_UHR = {80.0: 20, 89.9: 12, 90.0: 11, 92.0: 10}


@pytest.mark.parametrize("soc, ist, minuten, modus, neu", [
    (80.0, 10, 45, "normal", True),         # +10 A: sofort
    (80.0, 11, 45, "normal", False),        # +9 A: wartet
    (80.0, 15, 60, "normal", True),         # +5 A nach einer Stunde Ruhe
    (80.0, 15, 59, "normal", False),        # +5 A, noch keine Stunde
    (80.0, 16, 60, "normal", False),        # +4 A: auch nach einer Stunde zu wenig
    (80.0, 50, 45, "normal", True),         # -30 A: sofort
    (80.0, 49, 45, "normal", False),        # -29 A: wartet
    (80.0, 40, 60, "normal", True),         # -20 A nach einer Stunde
    (80.0, 40, 59, "normal", False),        # -20 A, noch keine Stunde
    (89.9, 60, 45, "normal", True),         # -48 A unter 90 %: abgesenkt
    (90.0, 60, 45, "normal", False),        # -49 A ab 90 %: nie abgesenkt
    (92.0, 0, 45, "normal", True),          # +10 A: angehoben wird auch ueber 90 %
    (80.0, 21, 45, "peak_shaving", True),   # anderer Zweig schrieb zuletzt: -1 A sofort
    (92.0, 9, 45, "peak_shaving", True),    # ebenso +1 A ueber 90 %
    (92.0, 11, 45, "peak_shaving", False),  # aber ueber 90 % nicht nach unten
])
def test_prio7_schwellen_fuer_anheben_und_absenken(blueprint, tag, soc, ist, minuten, modus, neu):
    """
    Anheben ab 10 A sofort, ab 5 A nach einer Stunde Ruhe; absenken nur unter 90 % und
    erst ab 30 A, nach einer Stunde ab 20 A. Kleinere Schritte kosten Registerschreiben,
    ohne dass die Batterie schneller voll wird; ueber 90 % verzoegerte ein Absenken das
    Vollwerden. Hat ein anderer Zweig zuletzt geschrieben, zieht Prio 7 sofort nach.
    """
    from conftest import prognose
    jetzt = zeit(tag, 10, 0)
    ov = {**_z(blueprint, "wr_max_charge_current", str(ist), last_changed=jetzt - dt.timedelta(minutes=minuten)),
          **_z(blueprint, "helper_lade_modus", modus)}
    h = szenario(blueprint, jetzt, soc=soc, forecast=prognose(tag, [4.0] * 20, dt.time(8, 0), p10_anteil=1.0),
                 profil_kwh=0.2, zustands_overrides=ov)
    ctx = h.auswerten()
    alias, aktionen = zweig_und_aktionen(h, blueprint, ctx)
    assert alias.startswith("PRIO 7") and ctx["target_p5"] == ZIELSTROM_10_UHR[soc]
    assert schreibt(aktionen, "number.set_value", ctx["var_wr_max_charge"]) == ([ZIELSTROM_10_UHR[soc]] if neu else [])


@pytest.mark.parametrize("offset, erwartet", [
    (2.0, 9),    # min_ampere 7 A + 2 A
    (2.3, 10),   # 9,3 A aufgerundet
])
def test_zellausgleich_laedt_mit_mindeststrom_und_regelabweichung(blueprint, tag, offset, erwartet):
    """Der kleine Reststrom muss wirklich an der Batterie ankommen, deshalb mit Regelabweichung und aufgerundet."""
    h = szenario(blueprint, zeit(tag, 12, 0), soc=98.0, input_overrides={"regler_offset_a": offset},
                 zustands_overrides=_z(blueprint, "vmax1_sensor", "3.45"))
    alias, aktionen = zweig_und_aktionen(h, blueprint)
    assert alias.startswith("PRIO 1") and schreibt(aktionen) == [erwartet]


@pytest.mark.parametrize("timer, vmax, ist, erwartet", [
    ("idle", "3.45", 200, None),      # heute schon voll: die Schwelle startet keinen zweiten Ausgleich
    ("active", "3.30", 200, [9]),     # Nachlauf: haelt 9 A, auch unter der Schwelle
    ("active", "3.30", 7, [9]),       # 2 A daneben: nachgeschrieben
    ("active", "3.30", 10, []),       # 1 A daneben: stehen gelassen
])
def test_zellausgleich_sperre_und_nachlauf(blueprint, tag, timer, vmax, ist, erwartet):
    """
    Der Ausgleich startet einmal am Tag und laeuft dann ueber den Nachlauf-Timer, nicht ueber
    die Zellspannung. Ein zweiter Start setzte Timer und Markierung erneut; nachgeschrieben
    wird erst ab 2 A Abweichung, darunter gilt der Sollwert als uebernommen.
    """
    ov = {**_z(blueprint, "helper_batterie_heute_voll", "on"), **_z(blueprint, "helper_timer_cooldown", timer),
          **_z(blueprint, "vmax1_sensor", vmax)}
    h = szenario(blueprint, zeit(tag, 12, 0), soc=98.0, ladestrom=ist, zustands_overrides=ov)
    alias, aktionen = zweig_und_aktionen(h, blueprint)
    if erwartet is None:
        assert alias is None and aktionen == []
    else:
        assert alias.startswith("PRIO 1") and schreibt(aktionen) == erwartet
        assert "timer.start" not in [s for s, _, _ in aktionen]


@pytest.mark.parametrize("soc, prio, strom, text", [
    (88.7, "PRIO 2", 350, "da Bedarf 4.36 kWh > Verfügbar 4.32 kWh"),
    (88.9, "PRIO 7", 26, "mit 24 A + 2.0 A Regelabweichung bis Ende des Ladefensters gedeckt wird; "
                         "bis 1 h davor reicht der Überschuss nicht mehr"),
])
def test_kippstelle_von_fall_b_in_der_kaskade(blueprint, tag, soc, prio, strom, text):
    """
    Die Lage aus test_kippstelle_von_fall_b: knapp darueber reisst Prio 2 auf das Maximum
    350 A auf (bei 22 Grad kein Temperaturdeckel), knapp darunter regelt Prio 7. Deren
    Zielstrom: bis 1 h vor Fensterende kommen hoechstens 4 x 0,8 = 3,2 kWh an, zu wenig fuer
    3,57 kWh; im vollen Fenster 3,57 / (6 x 0,0256) = 23,2 -> 24 A, + 2 A.
    """
    from conftest import prognose
    e = fake_entity("pv_erzeugung_heute_sensor", "sensor")  # Realitaets-Check kuerzt nicht
    h = szenario(blueprint, zeit(tag, 15, 0), soc=soc, forecast=prognose(tag, [2.0] * 20, dt.time(8, 0), p10_anteil=1.0),
                 profil_kwh=0.2, zustands_overrides={e: Zustand(e, "999")})
    alias, aktionen = zweig_und_aktionen(h, blueprint)
    assert alias.startswith(prio) and schreibt(aktionen) == [strom]
    meldung = " ".join(schreibt(aktionen, "logbook.log")[0].split())
    assert text in meldung, meldung
    # Die Uhrzeit waere das Ende des um den Vorlauf gekuerzten Fensters, das hier nicht reicht.
    assert prio != "PRIO 7" or "Voraussichtlich voll" not in meldung, meldung


@pytest.mark.parametrize("soc, ist, strom, text, voll_um", [
    (94.0, 20, 33, "mit 31 A + 2.0 A Regelabweichung bis Ende des Ladefensters gedeckt wird (freie", False),
    (96.0, 40, 53, "mit 51 A + 2.0 A Regelabweichung in der nächsten halben Stunde gedeckt wird, da vom restlichen "
                   "Ladefenster 1.25 h nach Abzug des Ladevorlaufs 1 h keine halbe Stunde bleibt (freie", True),
])
def test_prio7_meldung_bei_kurzem_ladefenster(blueprint, tag, soc, ist, strom, text, voll_um):
    """
    16:45, 3 kW bis 18:00: Vom Ladefenster bleiben 1,25 h, der erste Slot ist angebrochen, und nach 1 h
    Vorlauf bleibt keine halbe Stunde. Die Kurzfassung rechnet dann mit der naechsten halben Stunde, die
    Uhrzeit ist deren Ende, 17:15. 94 %: 1,93 kWh liefert die naechste halbe Stunde nicht (1,48 kWh
    Ueberschuss), das volle Fenster traegt: 1,93 / (1,25 h x 0,0512 kWh je A und Stunde) = 30,1 -> 31 A
    + 2 A; Vorlauf-Satz und Uhrzeit fehlen. 96 %: 1,29 kWh / (0,5 h x 0,0512) = 50,2 -> 51 A + 2 A.
    """
    from conftest import prognose
    e = fake_entity("pv_erzeugung_heute_sensor", "sensor")  # Realitaets-Check kuerzt nicht
    h = szenario(blueprint, zeit(tag, 16, 45), soc=soc, ladestrom=ist, forecast=prognose(tag, [3.0] * 20, dt.time(8, 0), p10_anteil=1.0),
                 profil_kwh=0.2, zustands_overrides={e: Zustand(e, "999")})
    alias, aktionen = zweig_und_aktionen(h, blueprint)
    assert alias.startswith("PRIO 7") and schreibt(aktionen) == [strom]
    meldung = " ".join(schreibt(aktionen, "logbook.log")[0].split())
    assert text in meldung and "davor reicht" not in meldung, meldung
    assert ("Voraussichtlich voll um 17:15 Uhr." in meldung) is voll_um, meldung


def test_prio7_meldung_ohne_ladevorlauf(blueprint, tag):
    """
    10:00, 3 kW bis 18:00, Ladevorlauf 0: Das gekuerzte Fenster ist das volle, 16 Halbstunden mit je
    1,5 - 0,2 = 1,3 kWh. 85 %: 4,82 kWh / (8 h x 0,0512 kWh je A und Stunde) = 11,8 -> 12 A + 2 A.
    Ohne Vorlauf heisst die Grenze Ende des Ladefensters, nicht "0 h davor".
    """
    from conftest import prognose
    e = fake_entity("pv_erzeugung_heute_sensor", "sensor")  # Realitaets-Check kuerzt nicht
    h = szenario(blueprint, zeit(tag, 10, 0), soc=85.0, ladestrom=200, forecast=prognose(tag, [3.0] * 20, dt.time(8, 0), p10_anteil=1.0),
                 profil_kwh=0.2, zustands_overrides={e: Zustand(e, "999")}, input_overrides={"ladevorlauf_stunden": "00:00:00"})
    alias, aktionen = zweig_und_aktionen(h, blueprint)
    assert alias.startswith("PRIO 7") and schreibt(aktionen) == [14]
    meldung = " ".join(schreibt(aktionen, "logbook.log")[0].split())
    assert "mit 12 A + 2.0 A Regelabweichung bis Ende des Ladefensters gedeckt wird (freie" in meldung, meldung
    assert "Voraussichtlich voll um 18:00 Uhr." in meldung, meldung


def test_prio7_meldung_bei_linearer_verteilung(blueprint, tag):
    """
    15:00, 0,6 kW bis 18:00: je Halbstunde 0,3 - 0,2 = 0,1 kWh, sechs Halbstunden 0,6 kWh gegen 1,0 kWh
    Bedarf (unter der Bagatellgrenze, also kein Fall B). Auch der Hoechststrom deckt ihn nicht, die
    Simulation findet keinen Strom: 1,0 kWh / 3 h / 51,2 V = 6,5 A + 2 A = 8,5 -> 9 A. Eine Uhrzeit
    gibt es dann nicht.
    """
    from conftest import prognose
    e = fake_entity("pv_erzeugung_heute_sensor", "sensor")  # Realitaets-Check kuerzt nicht
    h = szenario(blueprint, zeit(tag, 15, 0), soc=96.9, ladestrom=0, forecast=prognose(tag, [0.6] * 20, dt.time(8, 0), p10_anteil=1.0),
                 profil_kwh=0.2, zustands_overrides={e: Zustand(e, "999"), **_z(blueprint, "helper_lade_modus", "peak_shaving")})
    alias, aktionen = zweig_und_aktionen(h, blueprint)
    assert alias.startswith("PRIO 7") and schreibt(aktionen) == [9]
    meldung = " ".join(schreibt(aktionen, "logbook.log")[0].split())
    assert ("mit 6.5 A + 2.0 A Regelabweichung gleichmäßig über 3.0 h mit PV-Überschuss verteilt wird; der Überschuss "
            "im Ladefenster deckt ihn auch mit 350 A nicht (freie") in meldung, meldung
    assert "Voraussichtlich voll" not in meldung and "gedeckt wird" not in meldung, meldung


@pytest.mark.parametrize("temp, strom, genannt", [
    ("22.0", 14, False),   # kein Deckel
    ("12.0", 14, False),   # Deckel 188 A liegt unter dem Maximum, aber weit ueber dem Sollwert
    ("3.0", 10, True),     # Deckel 10 A unter dem Sollwert 14 A: er begrenzt
])
def test_temperaturdeckel_nur_genannt_wenn_er_begrenzt(blueprint, tag, temp, strom, genannt):
    """
    Die Szene aus test_prio7_meldung_ohne_ladevorlauf (Sollwert 14 A) und Prio 5 mit 25 A: Der
    Deckel steht nur in der Meldung, wenn er den geschriebenen Wert kleiner macht - ein Deckel
    unter dem Maximum allein erklaert keinen Sollwert, der weit darunter liegt.
    """
    from conftest import prognose
    kalt = {**_z(blueprint, "bms1_temp_min_sensor", temp), **_z(blueprint, "bms2_temp_min_sensor", temp)}
    e = fake_entity("pv_erzeugung_heute_sensor", "sensor")  # Realitaets-Check kuerzt nicht
    h = szenario(blueprint, zeit(tag, 10, 0), soc=85.0, ladestrom=0, forecast=prognose(tag, [3.0] * 20, dt.time(8, 0), p10_anteil=1.0),
                 profil_kwh=0.2, zustands_overrides={e: Zustand(e, "999"), **kalt}, input_overrides={"ladevorlauf_stunden": "00:00:00"})
    alias, aktionen = zweig_und_aktionen(h, blueprint)
    assert alias.startswith("PRIO 7") and schreibt(aktionen) == [strom]
    meldung = " ".join(schreibt(aktionen, "logbook.log")[0].split())
    assert ("Begrenzung durch Batterietemperatur" in meldung) is genannt, meldung

    ov = {**_z(blueprint, "helper_timer_peak", "active"), **_z(blueprint, "grid_export_sensor", "-8000"), **kalt}
    h = szenario(blueprint, zeit(tag, 12, 0), soc=84.0, ladestrom=0, zustands_overrides=ov)
    alias, aktionen = zweig_und_aktionen(h, blueprint)
    assert alias.startswith("PRIO 5") and schreibt(aktionen) == [10 if genannt else 25]
    meldung = " ".join(schreibt(aktionen, "logbook.log")[0].split())
    assert ("Durch die Batterietemperatur auf" in meldung) is genannt, meldung


# --------------------------------------------------------------------------
# Was die Zweige neben der Kaskade schreiben. Nicht fuer jeden - nur dort, wo
# der Schreibvorgang selbst eine Aussage traegt.
# --------------------------------------------------------------------------
def test_sonnenuntergang_parkt_den_verlust_kandidaten(blueprint, tag):
    """
    Der Kandidat ist die ungenutzte Ladekapazitaet beim hoechsten Tages-SOC,
    gedeckelt auf die Einspeisung des Tages: Mehr als eingespeist wurde, kann
    nicht verschenkt worden sein. Ob daraus ein Verlust wird, entscheidet erst
    die Nacht.
    """
    h = sonnenuntergang(blueprint, tag)
    ctx = h.auswerten()
    _, aktionen = zweig_und_aktionen(h, blueprint, ctx, zweige=gruppe(blueprint, "Aktionen bei Sonnenuntergang"))
    freie_kapazitaet = ctx["batterie_kapazitaet"] * (1 - float(h.states(ctx["var_max_soc_heute"])) / 100)
    export = float(h.states(ctx["var_grid_export_kwh"]))
    assert freie_kapazitaet > export, "sonst prueft das Szenario den Deckel nicht"
    assert schreibt(aktionen, "input_number.set_value", ctx["var_offener_verlust"]) == [pytest.approx(export)]


def _sonnenuntergang_nach_halten(blueprint, tag, *, tou, max_soc, kw, **ov):
    """Die Untergrenze hielt nachts (1,234 kWh Netzbezug), die Batterie wurde nicht voll."""
    from conftest import prognose
    zs = {**_z(blueprint, "helper_halten_bezug", "1.234"), **_z(blueprint, "helper_max_soc_heute", str(max_soc))}
    for i in range(1, 7):
        zs.update(_z(blueprint, f"wr_tou_{i}", str(tou)))
    zs.update(ov)
    return szenario(blueprint, zeit(tag, 18, 30), soc=60.0, forecast=prognose(tag, [kw] * 16, dt.time(9, 0), p10_anteil=1.0),
                    profil_kwh=0.2, trigger_id="sunset_check", zustands_overrides=zs)


@pytest.mark.parametrize("tou, max_soc, text", [
    # Die Prognose fuer heute (16 x 1,0 kWh) haette von 60 % aus das Ziel erreicht: kein Urteil daraus
    (60, 72, "Batterie heute nicht voll, höchster Tages-Ladestand (SOC) 72 % < Ziel-Ladestand 90 %."),
    # Abgerundet, damit 89,6 % nicht als erreichtes Ziel 90 % dasteht
    (50, 89.6, "Batterie heute nicht voll, höchster Tages-Ladestand (SOC) 89 % < Ziel-Ladestand 90 %."),
    # Ein Halten am Nachmittag hat das Register auf 77 % gehoben
    (77, 78, "Batterie heute nicht voll, höchster Tages-Ladestand (SOC) 78 % < Ziel-Ladestand 90 %."),
    # Ziel erreicht, nur nicht voll
    (60, 93, "Batterie heute nicht voll, Ziel-Ladestand aber erreicht: höchster Tages-Ladestand (SOC) 93 % ≥ Ziel-Ladestand 90 %."),
])
def test_nicht_voll_nach_haltenacht_ohne_urteil_ueber_die_planung(blueprint, tag, tou, max_soc, text):
    """
    Ob die Planung auf heute zaehlte, laesst sich am Abend nicht pruefen: Das Register kann ein Halten am
    Nachmittag schon angehoben haben, und Solcast hat die Prognose fuer heute an den Ertrag angeglichen.
    Die Zeile nennt deshalb nur Netzbezug und Tageshoechststand gegen das Ziel, ohne "zu mutig" und ohne
    Aussage ueber die Prognose. Eine Diagnose je Abend wie bisher, der Helfer wird danach geleert.
    """
    h = _sonnenuntergang_nach_halten(blueprint, tag, tou=tou, max_soc=max_soc, kw=2.0)
    ctx = h.auswerten()
    _, aktionen = zweig_und_aktionen(h, blueprint, ctx, zweige=gruppe(blueprint, "Aktionen bei Sonnenuntergang"))
    diagnose = [" ".join(m.split()) for m in schreibt(aktionen, "logbook.log") if "Entlade-" in m]
    assert len(diagnose) == 1, diagnose
    assert "Entlade-Untergrenze hielt nachts bei Netzbezug 1.23 kWh, " + text in diagnose[0], diagnose[0]
    assert "mutig" not in diagnose[0] and "Prognose" not in diagnose[0]
    assert schreibt(aktionen, "input_number.set_value", ctx["var_halten_bezug"]) == [0]


def _blockade_austritt(blueprint, h, ctx):
    """Der Block, der das Ende der Morgen-Blockade erkennt: ob er feuert und seine Meldung."""
    schritt = next(s for s in blueprint["action"] if isinstance(s, dict) and "if" in s
                   and "aktueller_modus == 'blockade' and not blockade_aktiv" in " ".join(str(s["if"]).split()))
    feuert = all(h._aufloesen(b["value_template"], ctx) for b in schritt["if"])
    meldung = next(a for a in schritt["then"] if a.get("action") == "logbook.log")["data"]["message"]
    return feuert, " ".join(str(h._aufloesen(meldung, ctx)).split())


def test_blockade_ende_nennt_beide_vergleichswerte(blueprint, tag):
    """
    09:00, Prognose 3,6 kW von 08:00 bis 12:00 und von 16:00 bis 20:00, dazwischen 0,1 kW; um 1/1,2
    gekuerzt, Haus 0,2 kWh je Halbstunde. Ab 09:30 bis zum Fensterende minus 1 h Vorlauf (19:00):
    11 Halbstunden mit 1,5 - 0,2 = 1,3 kWh = 14,3 kWh, Puffer 1 kWh -> Verfuegbar 13,3 kWh. Die acht
    Halbstunden der Luecke (0,0417 - 0,2) holt der Nachmittag auf: Nachladebedarf 1,27 kWh.
    68 %: (10,29 + 1,27) x 1,2 = 13,87 kWh > 13,3 kWh, die Blockade endet. Ohne den Nachladebedarf
    stuenden 12,35 kWh gegen 13,3 kWh, und die Meldung widerspraeche sich. 70 %: 13,10 kWh, sie haelt.
    """
    from conftest import prognose
    fc = prognose(tag, [3.6] * 8 + [0.1] * 8 + [3.6] * 8, dt.time(8, 0), p10_anteil=1.0)
    zs = {**_z(blueprint, "helper_lade_modus", "blockade"), **_z(blueprint, "helper_blockade_beendet", "off")}
    ergebnis = {}
    for soc in (68.0, 70.0):
        h = szenario(blueprint, zeit(tag, 9, 0), soc=soc, forecast=fc, profil_kwh=0.2, zustands_overrides=zs,
                     input_overrides={"schwelle_peak_shaving": 2500})
        ctx = h.auswerten()
        assert ctx["spitze_erwartet"] is True and ctx["trend_aktiv"] is False
        ergebnis[soc] = _blockade_austritt(blueprint, h, ctx)
    assert ergebnis[70.0][0] is False
    feuert, meldung = ergebnis[68.0]
    assert feuert is True
    assert ("da der späteste Ladebeginn erreicht ist: Bedarf 13.9 kWh > Verfügbar 13.3 kWh ab der nächsten Halbstunde "
            "(Bedarf = freie Ladekapazität 10.3 kWh von 32.2 kWh + Nachladebedarf 1.3 kWh für Stunden mit Hausverbrauch "
            "über PV × Faktor 1.2; Verfügbar = Überschuss 14.3 kWh − Puffer 1.0 kWh).") in meldung, meldung


def test_blockade_ende_ohne_halbstunde_vor_der_obergrenze(blueprint, tag):
    """
    Obergrenze 13:15, reichlich Sonne: Um 12:50 ist 13:00 der letzte moegliche Ladebeginn und traegt, die
    Blockade haelt. Um 13:05 beginnt vor 13:15 keine Halbstunde mehr, T ist leer, obwohl der Bedarf
    gedeckt waere; die Meldung nennt deshalb die fehlende Halbstunde, keinen Vergleich Bedarf > Verfuegbar.
    """
    from conftest import prognose
    fc = prognose(tag, [6.0] * 20, dt.time(8, 0), p10_anteil=1.0)
    zs = {**_z(blueprint, "helper_lade_modus", "blockade"), **_z(blueprint, "helper_blockade_beendet", "off")}
    ergebnis = {}
    for minute in ((12, 50), (13, 5)):
        h = szenario(blueprint, zeit(tag, *minute), soc=85.0, forecast=fc, profil_kwh=0.2, zustands_overrides=zs,
                     input_overrides={"schwelle_peak_shaving": 2500, "zeit_blockade_ende": "13:15:00"})
        ctx = h.auswerten()
        ergebnis[minute] = (ctx["blockade_dyn_kand"], _blockade_austritt(blueprint, h, ctx))
    assert ergebnis[(12, 50)][0] == 1 and ergebnis[(12, 50)][1][0] is False
    kand, (feuert, meldung) = ergebnis[(13, 5)]
    assert kand == 0 and feuert is True
    assert "da vor der eingestellten Obergrenze 13:15 Uhr keine Halbstunde mehr als Ladebeginn in Frage kommt." in meldung, meldung
    assert "Bedarf" not in meldung, meldung


def _benachrichtigungs_ids(knoten, dienst):
    if isinstance(knoten, dict):
        eigen = [knoten.get("data", {}).get("notification_id")] if knoten.get("action") == dienst else []
        return eigen + [i for v in knoten.values() for i in _benachrichtigungs_ids(v, dienst)]
    if isinstance(knoten, list):
        return [i for v in knoten for i in _benachrichtigungs_ids(v, dienst)]
    return []


def test_nicht_voll_hinweis_nennt_den_ueberfaelligen_zellausgleich(blueprint, tag):
    """
    Mit Entlade-Planung und ueberfaelligem Zellausgleich (zuletzt voll vor 12 Tagen, faellig nach 9) fehlt Sonne,
    nicht die Auslegung: kein Rat, die Auslegung zu pruefen. Ohne Planung bleibt er. Feste Kennung: ein neuer
    Abend ersetzt den Hinweis, der naechste Zellausgleich (Prio 1 markiert die Batterie als voll) entfernt ihn.
    """
    import json
    zs = {**_z(blueprint, "json_tracking_sensor", json.dumps([(tag - dt.timedelta(days=12)).isoformat()])),
          **_z(blueprint, "eingriff_dauer_sensor", "2.3"), **_z(blueprint, "helper_max_soc_heute", "88")}
    zweige = gruppe(blueprint, "Aktionen bei Sonnenuntergang")
    mit = szenario(blueprint, zeit(tag, 18, 30), soc=60.0, trigger_id="sunset_check", zustands_overrides=zs)
    ctx = mit.auswerten()
    assert ctx["zellausgleich_faellig"] is True and ctx["ziel_soc_eff"] == 100
    _, aktionen = zweig_und_aktionen(mit, blueprint, ctx, zweige=zweige)
    hinweis = schreibt(aktionen, "persistent_notification.create")
    assert len(hinweis) == 1 and "Auslegung" not in hinweis[0]
    assert "Zellausgleich seit 3 Tagen überfällig" in hinweis[0]
    assert "Höchster Tages-Ladestand (SOC) 88 %, Ziel-Ladestand 100 %" in hinweis[0]
    ohne = szenario(blueprint, zeit(tag, 18, 30), soc=60.0, trigger_id="sunset_check", prognose_tage_kwh=None, zustands_overrides=zs)
    _, aktionen = zweig_und_aktionen(ohne, blueprint, zweige=zweige)
    assert "Bitte die Auslegung/Logik prüfen" in schreibt(aktionen, "persistent_notification.create")[0]
    angelegt = [i for i in _benachrichtigungs_ids(blueprint["action"], "persistent_notification.create") if i not in ("bms_offline_warning", "soc_fehlt_warning")]
    assert angelegt == ["batterie_nicht_voll"]
    prio1 = next(z for z in gruppe(blueprint, "PRIO 0") if z["alias"].startswith("PRIO 1"))
    assert _benachrichtigungs_ids(prio1, "persistent_notification.dismiss") == ["batterie_nicht_voll"]


def test_tou_minimum_setzt_alle_sechs_register(blueprint, tag):
    """Ohne Entlade-Planung gehoeren die Register niemandem - sie fallen auf den Mindestwert zurueck."""
    h = tou_minimum(blueprint, tag)
    ctx = h.auswerten()
    _, aktionen = zweig_und_aktionen(h, blueprint, ctx, zweige=gruppe(blueprint, "ToU auf Minimum"))
    ziele = [e for s, e, _ in aktionen if s == "number.set_value"]
    assert schreibt(aktionen) == [ctx["default_tou_soc"]]
    assert ziele == [[standard_inputs(blueprint)[f"wr_tou_{i}"] for i in range(1, 7)]]


def test_peak_shaving_ende_trennt_abbruch_vom_ablauf(blueprint, tag):
    """
    Der Trigger feuert bei abgelaufenem UND bei abgebrochenem Timer. Beide
    Faelle bekommen ihre eigene Meldung; auf den Wortlaut kommt es nicht an,
    nur darauf, dass keiner der beiden Wege tot ist.
    """
    zweige = gruppe(blueprint, "PEAK-SHAVING ENDE")
    ohne_sperre = peak_shaving_ende(blueprint, tag)
    mit_sperre = szenario(blueprint, zeit(tag, 12, 0), soc=84.0, trigger_id="peak_shaving_ende",
                          input_overrides={"wp_boost_aktiv": True},
                          zustands_overrides=_z(blueprint, "helper_timer_wp_anlauf", "active"))
    assert ohne_sperre.auswerten()["wp_sperre_aktiv"] is False
    assert mit_sperre.auswerten()["wp_sperre_aktiv"] is True
    _, abgelaufen = zweig_und_aktionen(ohne_sperre, blueprint, zweige=zweige)
    _, abgebrochen = zweig_und_aktionen(mit_sperre, blueprint, zweige=zweige)
    assert len(schreibt(abgelaufen, "logbook.log")) == 1
    assert schreibt(abgebrochen, "logbook.log") != schreibt(abgelaufen, "logbook.log")


def test_wp_boost_start_hebt_das_ziel_und_startet_beide_timer(blueprint, tag):
    """
    Zieltemperatur, Hysterese und beide Timer gehoeren zusammen: Ohne den
    Anlauf-Timer nimmt Prio 3 der anlaufenden Pumpe den Ueberschuss nicht frei,
    ohne den Laufzeit-Timer faellt das Ziel nie auf den Normalwert zurueck.
    """
    h = wp_boost_start(blueprint, tag)
    ctx = h.auswerten()
    _, aktionen = zweig_und_aktionen(h, blueprint, ctx, zweige=gruppe(blueprint, "WP-BOOST: START"))
    assert schreibt(aktionen, "water_heater.set_temperature") == [ctx["var_wp_ziel_temp_pv"]]
    assert schreibt(aktionen, "number.set_value", ctx["var_wp_ziel_number"]) == [ctx["var_wp_ziel_temp_pv"]]
    assert schreibt(aktionen, "number.set_value", ctx["var_wp_hysterese_number"]) == [ctx["var_wp_hysterese_pv"]]
    assert [e for s, e, _ in aktionen if s == "timer.start"] == [ctx["var_timer_wp_anlauf"], ctx["var_timer_wp_boost"]]


def test_boost_laesst_den_fast_vollen_speicher_in_ruhe(blueprint, tag):
    """
    Die Boost-Hysterese entscheidet, wie leer der Speicher sein muss, bevor sich
    eine Ladung lohnt: Leitung und Anlauf kosten je Ladung dasselbe, ein kurzer
    Nachschlag traegt diesen Festbetrag also auf wenige Kelvin. Mit der Vorgabe
    bleibt ein Speicher knapp unter der alten 54-Grad-Marke unangetastet.
    """
    ov = _z(blueprint, "grid_export_sensor", "-8000")
    startwert = (standard_inputs(blueprint)["wp_ziel_temp_pv"]
                 - standard_inputs(blueprint)["wp_hysterese_pv"])
    assert startwert <= 49, "Vorgabe der Boost-Hysterese ist wieder zu eng geworden"
    voll = szenario(blueprint, zeit(tag, 12, 0),
                    zustands_overrides={**ov, **_z(blueprint, "wp_temp_sensor", "53.0")})
    leer = szenario(blueprint, zeit(tag, 12, 0),
                    zustands_overrides={**ov, **_z(blueprint, "wp_temp_sensor", str(startwert - 1))})
    assert voll.auswerten()["wp_boost_startet_gleich"] is False
    assert leer.auswerten()["wp_boost_startet_gleich"] is True


# --------------------------------------------------------------------------
# Das Tag-Tor: Warmwasser ohne Einspeisespitze
# --------------------------------------------------------------------------
def _tag_tor_szenario(blueprint, tag, **kw):
    """
    Die Batterie laedt mit 8 kW, ins Netz gehen 200 W: Das Einspeise-Tor bleibt
    damit zu, der Ueberschuss ist trotzdem da. Warmwasser unter der normalen
    Zieltemperatur, Ladestand hoch genug, dass die Prognose den Rest hergibt.
    """
    ov = {**_z(blueprint, "grid_export_sensor", "-200"),
          **_z(blueprint, "battery_power_sensor", "-8000"),
          **_z(blueprint, "wp_temp_sensor", "45.0")}
    ov.update(kw.pop("zustands_overrides", {}))
    inputs = {"ww_energie_kwh": 2.0}
    inputs.update(kw.pop("input_overrides", {}))
    return szenario(blueprint, zeit(tag, 12, 0), soc=88.0,
                    input_overrides=inputs, zustands_overrides=ov, **kw)


def _boost_startet(h, blueprint) -> bool:
    z = gewinner(h, gruppe(blueprint, "WP-BOOST: START"))
    return z is not None and z["alias"].startswith("WP-BOOST: START")


def test_tag_tor_startet_den_boost_ohne_einspeisespitze(blueprint, tag):
    """
    Der Kern der Sache: Ohne Einspeisung ueber der Schwelle loeste frueher nichts
    aus, obwohl der Tag den Ueberschuss hergibt. Das Tag-Tor entscheidet
    stattdessen an der Tagesbilanz.
    """
    h = _tag_tor_szenario(blueprint, tag)
    ctx = h.auswerten()
    assert ctx["real_export"] < ctx["schwelle_wp_boost"], "Szenario prueft sonst das alte Tor"
    assert ctx["ww_tag_frei"] and ctx["ww_tag_start"]
    assert _boost_startet(h, blueprint)


def test_tag_tor_hebt_ziel_und_startet_beide_timer(blueprint, tag):
    """Der Tag-Pfad schreibt dasselbe wie der Einspeise-Pfad - nur das Tor ist anders."""
    h = _tag_tor_szenario(blueprint, tag)
    ctx = h.auswerten()
    _, aktionen = zweig_und_aktionen(h, blueprint, ctx, zweige=gruppe(blueprint, "WP-BOOST: START"))
    assert schreibt(aktionen, "water_heater.set_temperature") == [ctx["var_wp_ziel_temp_pv"]]
    assert schreibt(aktionen, "number.set_value", ctx["var_wp_hysterese_number"]) == [ctx["var_wp_hysterese_pv"]]
    assert [e for s, e, _ in aktionen if s == "timer.start"] == [ctx["var_timer_wp_anlauf"], ctx["var_timer_wp_boost"]]


def test_ohne_energiefeld_bleibt_das_tag_tor_zu(blueprint, tag):
    """
    ww_energie_kwh ist der Schalter. Auf 0 - dem Standard - verhaelt sich der
    Blueprint wie vorher, bestehende Instanzen aendern ihr Verhalten also nicht.
    """
    h = _tag_tor_szenario(blueprint, tag, input_overrides={"ww_energie_kwh": 0})
    assert not h.auswerten()["ww_tag_frei"]
    assert not _boost_startet(h, blueprint)


def test_tag_tor_laesst_die_batterie_puffern(blueprint, tag):
    """
    Die Leistungsseite verlangt nur, dass die PV den Ladeplan der Batterie deckt -
    die Aufnahme der Waermepumpe nicht. Faellt die PV kurz ab, puffert die Batterie
    sie; begrenzt wird das vom Abstand zur Entlade-Untergrenze, nicht hier.
    Eine gemessene Ladung zieht mehr, als an einem Herbsttag je momentan uebrig ist.
    """
    knapp = {**_z(blueprint, "grid_export_sensor", "0"),
             **_z(blueprint, "battery_power_sensor", "-2000")}
    h = _tag_tor_szenario(blueprint, tag, zustands_overrides=knapp)
    ctx = h.auswerten()
    basis = ctx["amp_basis_13"] * ctx["nominale_spannung"]
    assert basis < ctx["potential_export"] < basis + ctx["ww_energie_kwh"] * 1000, \
        "Szenario liegt sonst nicht in dem Band, das die beiden Lesarten trennt"
    assert ctx["ww_tag_frei"] and ctx["ww_tag_start"]
    assert _boost_startet(h, blueprint)


def test_tag_tor_wartet_ohne_batteriepuffer(blueprint, tag):
    """
    Reicht die PV im Moment nicht, muss die Batterie einspringen koennen - und
    das kann sie nur oberhalb der Entlade-Untergrenze. Register dicht unter dem
    Ladestand: der Puffer fehlt, der Boost wartet.
    """
    eng = {}
    for i in range(1, 7):
        eng.update(_z(blueprint, f"wr_tou_{i}", "86"))
    h = _tag_tor_szenario(blueprint, tag, zustands_overrides=eng)
    ctx = h.auswerten()
    assert ctx["ww_tag_frei"], "nur der Puffer soll fehlen, nicht die Tagesbilanz"
    assert ctx["ww_puffer_kwh"] < ctx["ww_energie_kwh"] + ctx["puffer_kwh"]
    assert not ctx["ww_tag_start"]
    assert not _boost_startet(h, blueprint)


def test_tag_tor_schweigt_waehrend_der_morgen_blockade(blueprint, tag):
    """
    Die Blockade haelt Platz fuer die erwartete Spitze frei und rechnet ihren
    Ladebeginn mit eigenem Puffer. Ueber die Einspeisung startet der Boost dort
    weiter, ueber die Tagesbilanz nicht.
    """
    h = szenario(blueprint, zeit(tag, 7, 0), soc=84.0,
                 input_overrides={"schwelle_peak_shaving": 3500, "ww_energie_kwh": 2.0},
                 zustands_overrides=_z(blueprint, "wp_temp_sensor", "45.0"))
    ctx = h.auswerten()
    assert ctx["blockade_aktiv"]
    assert not ctx["ww_tag_start"]
    assert not _boost_startet(h, blueprint)


# --------------------------------------------------------------------------
# Notbetrieb ohne Ladestand
# --------------------------------------------------------------------------
@pytest.mark.parametrize("soc, stunde, gegenprobe_soc, sonst", [
    ("unavailable", 12, 40.0, "PRIO 2"),   # als 0 % gerechnet raste sonst Fall B ein
    ("0", 12, 40.0, "PRIO 2"),             # eine gemeldete 0 ist kein Messwert
    ("255", 12, 84.0, "PRIO 7"),           # ueber 100 ebenso wenig
    ("unavailable", 7, 84.0, "PRIO 6"),    # morgens statt der Blockade
])
def test_notbetrieb_laedt_voll_ohne_einzurasten(blueprint, tag, soc, stunde, gegenprobe_soc, sonst):
    """
    Ohne Ladestand gewinnt Prio 8: voller Strom, und kein Modus, der den Tag ueber
    einrastet. Die Gegenprobe mit gueltigem Ladestand zeigt den Zweig, den der
    Notbetrieb verdraengt.
    """
    kw = {"input_overrides": {"schwelle_peak_shaving": 3500}, "ladestrom": 50.0}
    assert zweig(szenario(blueprint, zeit(tag, stunde, 0), soc=gegenprobe_soc, **kw), blueprint) == sonst
    h = szenario(blueprint, zeit(tag, stunde, 0), soc=soc, **kw)
    ctx = h.auswerten()
    alias, aktionen = zweig_und_aktionen(h, blueprint, ctx)
    assert alias.startswith("PRIO 8")
    assert schreibt(aktionen) == [ctx["max_ampere"]]
    assert schreibt(aktionen, "input_select.select_option") == []
    assert ctx["blockade_aktiv"] is False and ctx["tou_schreiben"] is False


def test_notbetrieb_wartet_einen_aussetzer_ab(blueprint, tag):
    """Fehlt der Wert erst seit zwei Minuten, schreibt kein Zweig; Fall B rastet trotzdem nicht ein."""
    ov = _z(blueprint, "battery_soc_sensor", "unavailable", last_changed=zeit(tag, 11, 58))
    h = szenario(blueprint, zeit(tag, 12, 0), ladestrom=50.0, zustands_overrides=ov)
    ctx = h.auswerten()
    assert ctx["soc_fehlt"] is True and ctx["soc_fehlt_bestaetigt"] is False
    assert zweig_und_aktionen(h, blueprint, ctx) == (None, [])


def test_notbetrieb_bleibt_unter_der_temperaturgrenze(blueprint, tag):
    """Kalte Zellen: Prio 8 laedt nur bis zum Temperaturdeckel, nicht bis zum Maximum."""
    ov = {**_z(blueprint, "battery_soc_sensor", "unavailable", last_changed=zeit(tag, 11, 0)),
          **_z(blueprint, "bms1_temp_min_sensor", "12.0"), **_z(blueprint, "bms2_temp_min_sensor", "12.0")}
    h = szenario(blueprint, zeit(tag, 12, 0), ladestrom=10.0, zustands_overrides=ov)
    ctx = h.auswerten()
    alias, aktionen = zweig_und_aktionen(h, blueprint, ctx)
    assert alias.startswith("PRIO 8")
    assert schreibt(aktionen) == [ctx["temperatur_limit_ampere"]]
    assert 10 < ctx["temperatur_limit_ampere"] < ctx["max_ampere"]


def test_notbetrieb_endet_mit_voller_batterie(blueprint, tag):
    """Hat der Zellausgleich die Batterie heute als voll markiert, laedt Prio 8 nicht erneut auf."""
    ov = {**_z(blueprint, "battery_soc_sensor", "unavailable", last_changed=zeit(tag, 11, 0)),
          **_z(blueprint, "helper_batterie_heute_voll", "on")}
    h = szenario(blueprint, zeit(tag, 12, 0), ladestrom=50.0, zustands_overrides=ov)
    assert zweig_und_aktionen(h, blueprint) == (None, [])


def test_notbetrieb_uebernimmt_von_laufender_lastspitzen_kappung(blueprint, tag):
    """
    Faellt der Ladestand bei laufendem Kappungs-Timer aus, gewinnt nicht Prio 5, die ohne
    Spitze nichts schreibt und den Strom bis zum Ablauf des Timers stehen liesse.
    """
    ov = _z(blueprint, "helper_timer_peak", "active")
    assert zweig(szenario(blueprint, zeit(tag, 12, 0), soc=84.0, ladestrom=50.0, zustands_overrides=ov), blueprint) == "PRIO 5"
    h = szenario(blueprint, zeit(tag, 12, 0), soc="unavailable", ladestrom=50.0, zustands_overrides=ov)
    alias, aktionen = zweig_und_aktionen(h, blueprint)
    assert alias.startswith("PRIO 8") and schreibt(aktionen) == [h.auswerten()["max_ampere"]]


@pytest.mark.parametrize("alias_anfang, bauen", [
    ("Speicherverlust verbuchen", verlust_verbuchen),
    ("ToU auf Minimum", tou_minimum),
    ("Zieht den Peak-Shaving Timer auf", peak_timer_aufziehen),
])
def test_ohne_ladestand_ruhen_verbuchung_register_und_timer(blueprint, tag, alias_anfang, bauen):
    """Dieselben Laeufe wie oben, nur ohne Ladestand: kein Zweig dieser Gruppen greift."""
    h = bauen(blueprint, tag)
    h.states.tabelle.update(_z(blueprint, "battery_soc_sensor", "unavailable", last_changed=h.jetzt - dt.timedelta(hours=1)))
    assert gewinner(h, gruppe(blueprint, alias_anfang)) is None
