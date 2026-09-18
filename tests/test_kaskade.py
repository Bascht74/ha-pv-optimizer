"""
Die Prioritaetskaskade: welcher Zweig gewinnt, und was er schreibt.

Die uebrigen Tests pruefen die Zahlen, die in die Entscheidung eingehen. Hier
geht es um die Entscheidung selbst - die Reihenfolge der Zweige und ihre
Kopplungen. test_jeder_zweig_ist_abgedeckt haelt die Abdeckung vollstaendig:
Kommt ein Zweig dazu, wird der Test rot, bis ein Szenario ihn erreicht.
"""
from __future__ import annotations

import datetime as dt

import pytest

from conftest import fake_entity, szenario, zeit
from ha_jinja import Zustand
from kaskade import kaskade, schreibt, zweig, zweig_und_aktionen

TIMER = ("helper_timer_peak", "helper_timer_wp_anlauf", "helper_timer_wp_boost", "helper_timer_cooldown")


def _z(name: str, wert: str, **kw) -> dict:
    """Ein Zustands-Override fuer eine der Fake-Entitaeten."""
    domain = "timer" if name in TIMER else (
        "binary_sensor" if name.endswith("_kompressor_sensor") else (
            "input_boolean" if name.startswith("helper_") else "sensor"))
    eid = fake_entity(name, domain)
    return {eid: Zustand(eid, wert, **kw)}


# --------------------------------------------------------------------------
# Je Zweig ein Szenario. Der Kommentar nennt die Bedingung, die ihn gewinnen
# laesst - nicht die Zahlen, die dabei herauskommen.
# --------------------------------------------------------------------------
def p0_temperaturdeckel(blueprint, tag):
    """Kalte Zellen senken die zulaessige Laderate unter den Sollwert."""
    ov = {**_z("bms1_temp_min_sensor", "12.0"), **_z("bms2_temp_min_sensor", "12.0")}
    return szenario(blueprint, zeit(tag, 12, 0), ladestrom=200.0, zustands_overrides=ov)


def p1_zellausgleich(blueprint, tag):
    """Eine Zellspannung erreicht die Balancing-Schwelle, die Batterie war heute noch nicht voll."""
    return szenario(blueprint, zeit(tag, 12, 0), soc=98.0, zustands_overrides=_z("vmax1_sensor", "3.45"))


def p2_fall_b(blueprint, tag):
    """Niedriger Ladestand: die Restzeit reicht nicht, der Strom wird aufgerissen."""
    return szenario(blueprint, zeit(tag, 12, 0), soc=40.0)


def p3_wp_anlaufsperre(blueprint, tag):
    """Der Anlauf-Timer der Waermepumpe laeuft, der Ladestrom wird gehalten."""
    return szenario(blueprint, zeit(tag, 12, 0), input_overrides={"wp_boost_aktiv": True},
                    zustands_overrides=_z("helper_timer_wp_anlauf", "active"))


def p4_wp_kompressor(blueprint, tag):
    """Der Verdichter laeuft, der Ueberschuss gehoert ihm."""
    return szenario(blueprint, zeit(tag, 12, 0), zustands_overrides=_z("wp_kompressor_sensor", "on"))


def p5_peak_shaving(blueprint, tag):
    """Der Kappungs-Timer laeuft und die Einspeisung liegt ueber der Schwelle."""
    ov = {**_z("helper_timer_peak", "active"), **_z("grid_export_sensor", "-8000")}
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


@pytest.mark.parametrize("prio, bauen", ZWEIGE, ids=[p for p, _ in ZWEIGE])
def test_zweig_gewinnt_in_seinem_szenario(blueprint, tag, prio, bauen):
    assert zweig(bauen(blueprint, tag), blueprint) == prio


def test_jeder_zweig_ist_abgedeckt(blueprint, tag):
    """
    Die Szenarien oben erreichen JEDEN Zweig der Kaskade. Kommt einer dazu oder
    wird einer so eng, dass ihn kein Szenario mehr trifft, wird dieser Test rot.
    """
    erwartet = {z["alias"].split(":")[0] for z in kaskade(blueprint)}
    getroffen = {zweig(bauen(blueprint, tag), blueprint) for _, bauen in ZWEIGE}
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
    ov = {**_z("helper_timer_peak", "active"), **_z("helper_timer_wp_anlauf", "active"),
          **_z("grid_export_sensor", "-8000")}
    h = szenario(blueprint, zeit(tag, 12, 0), soc=84.0, input_overrides={"wp_boost_aktiv": True},
                 zustands_overrides=ov)
    assert zweig(h, blueprint) == "PRIO 3"


def test_volle_batterie_laesst_kappung_und_nachladen_aus(blueprint, tag):
    """Ueber 99 % nimmt die Batterie nichts mehr auf: kein Zweig greift."""
    ov = {**_z("helper_timer_peak", "active"), **_z("helper_batterie_heute_voll", "on"),
          **_z("grid_export_sensor", "-8000")}
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
    ov = {**_z("helper_timer_peak", "active"), **_z("grid_export_sensor", "-8000"),
          eid: Zustand(eid, "200.0", last_changed=zeit(tag, 12, 0) - dt.timedelta(seconds=10))}
    h = szenario(blueprint, zeit(tag, 12, 0), soc=84.0, zustands_overrides=ov)
    alias, aktionen = zweig_und_aktionen(h, blueprint)
    assert alias.startswith("PRIO 5")
    assert schreibt(aktionen) == []
