"""
Strukturpruefungen - die Punkte aus CLAUDE.md, die sich mechanisch pruefen lassen.
Brauchen kein Harness, laufen in Millisekunden.
"""
from __future__ import annotations

import re

import pytest

from conftest import BLUEPRINT_PFAD, PACKAGE_PFAD
from ha_jinja import alle_input_tags, alle_template_strings, input_definitionen, lade_yaml


def test_yaml_parst_ohne_doppelte_keys():
    # lade_yaml wirft bei Duplikaten - SafeLoader wuerde sie still ueberschreiben.
    lade_yaml(BLUEPRINT_PFAD)


def test_package_parst():
    lade_yaml(PACKAGE_PFAD)


def test_jeder_input_wird_benutzt_und_jeder_benutzte_ist_definiert(blueprint):
    definiert = set(input_definitionen(blueprint))
    benutzt = alle_input_tags(blueprint["trigger_variables"]) | alle_input_tags(blueprint["trigger"]) | alle_input_tags(blueprint["action"])
    assert benutzt - definiert == set(), f"!input ohne Definition: {sorted(benutzt - definiert)}"
    assert definiert - benutzt == set(), f"Verwaiste Inputs: {sorted(definiert - benutzt)}"


def test_version_an_beiden_stellen_gleich(blueprint):
    im_namen = re.search(r"\[(V\d+\.\d+\.\d+)\]", blueprint["blueprint"]["name"])
    assert im_namen, "blueprint.name traegt keinen [Vx.y.z]-Tag"
    bp_version = next(s["variables"]["bp_version"] for s in blueprint["action"]
                      if isinstance(s, dict) and "bp_version" in s.get("variables", {}))
    assert bp_version == f"[{im_namen.group(1)}]", f"name={im_namen.group(0)} bp_version={bp_version}"


def test_jeder_entity_input_hat_leeren_default(blueprint):
    ohne = [name for name, d in input_definitionen(blueprint).items()
            if "entity" in (d.get("selector") or {}) and d.get("default") != ""]
    assert ohne == [], f"Entity-Inputs ohne default '': {ohne}"


def test_jinja_klammern_ausgeglichen(blueprint):
    # Auf dem geparsten Baum, nicht auf Zeilen: selector-Dicts sind keine Strings
    # und erzeugen hier keine Fehlalarme.
    schief = []
    for pfad, s in alle_template_strings(blueprint["action"]) + alle_template_strings(blueprint["trigger"]):
        if s.count("{{") != s.count("}}") or s.count("{%") != s.count("%}"):
            schief.append(f"{pfad}: {{{{={s.count('{{')} }}}}={s.count('}}')} {{%={s.count('{%')} %}}={s.count('%}')}")
    assert schief == [], "\n".join(schief)


def test_sektionsnamen_beginnen_nicht_mit_listenzeichen(blueprint):
    # "1." und "1)" rendert Home Assistant als geordnete Liste - jede Sektion
    # ist ein eigener Block, alle wuerden "1." heissen.
    schlecht = [d["name"] for d in blueprint["blueprint"]["input"].values()
                if isinstance(d, dict) and re.match(r"^\s*\d+[.)]\s", d.get("name", ""))]
    assert schlecht == [], schlecht


def test_beschreibungen_unter_300_zeichen(blueprint):
    zu_lang = {name: len(d.get("description", "") or "")
               for name, d in input_definitionen(blueprint).items()
               if len(d.get("description", "") or "") > 300}
    assert zu_lang == {}, zu_lang


def test_alle_kernvariablen_sind_top_level(blueprint):
    # Das Harness wertet nur top-level variables:-Schritte aus. Wandert eine
    # dieser Variablen in einen Zweig, faellt die Rechnungs-Testsuite still aus.
    namen = [k for s in blueprint["action"] if isinstance(s, dict) and "variables" in s for k in s["variables"]]
    for kern in ("json_profil", "slot_daten", "trend_daten", "pv_abschlag", "sim_a_fenster",
                 "blockade_dyn_json", "blockade_dyn", "wp_boost_karenz_ok", "fall_b_aktiv", "target_p5"):
        assert kern in namen, f"{kern} ist keine top-level Variable mehr"


@pytest.mark.parametrize("helfer_input", [
    "helper_lade_modus", "helper_batterie_heute_voll", "helper_blockade_beendet",
    "helper_zwangsladung_aktiv", "helper_max_soc_heute", "speicherverlust_gesamt",
    "helper_offener_verlust", "helper_tare_charge", "helper_tare_discharge",
    "json_tracking_sensor", "hausverbrauch_json_text", "helper_logbook_dummy",
    "helper_timer_peak", "helper_timer_cooldown", "helper_timer_wp_anlauf", "helper_timer_wp_boost",
])
def test_package_liefert_die_helfer_die_der_blueprint_erwartet(blueprint, package, helfer_input):
    """Jeder Helfer-Input des Blueprints hat im Package eine Entitaet derselben Domain."""
    domain = input_definitionen(blueprint)[helfer_input]["selector"]["entity"]["domain"]
    vorhanden = package.get(domain) or {}
    assert vorhanden, f"Package definiert keine {domain}-Entitaet fuer {helfer_input}"
