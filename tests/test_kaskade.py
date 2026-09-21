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
from kaskade import gewinner, gruppe, gruppen, schreibt, zweig, zweig_und_aktionen


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


ZWEIGE = [
    ("PRIO 0", p0_temperaturdeckel),
    ("PRIO 1", p1_zellausgleich),
    ("PRIO 2", p2_fall_b),
    ("PRIO 3", p3_wp_anlaufsperre),
    ("PRIO 4", p4_wp_kompressor),
    ("PRIO 5", p5_peak_shaving),
    ("PRIO 6", p6_morgen_blockade),
    ("PRIO 7", p7_dynamisch),
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
