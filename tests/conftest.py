"""
Fixtures fuer die Blueprint-Tests.

Die Szenarien sind synthetisch, aber in realistischen Wertebereichen: die
PV-Kurve ist die geglaettete Erzeugung eines echten Tages an einem der beiden
Standorte, die Batterie hat 2 x 314 Ah bei 51,2 V (32,2 kWh), das
Verbrauchsprofil liegt bei 0,19 kWh je Halbstunde.

Echte Laeufe kommen aus der Diagnose-Aufzeichnung des Blueprints (JSON-Zeilen
an notify.pv_optimizer_aufzeichnung, Einrichtung im Package-Kopf). Solche
Dateien gehoeren nach tests/fixtures/*.jsonl; szenario_aus_aufzeichnung baut
aus jeder Zeile ein Harness, die geloggten Entscheidungen des Tages sind der
Erwartungswert.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from ha_jinja import TZ, Harness, Zustand, input_definitionen, lade_yaml

WURZEL = Path(__file__).resolve().parent.parent
BLUEPRINT_PFAD = WURZEL / "PV-Ladesteuerung.yaml"
PACKAGE_PFAD = WURZEL / "pv-steuerung.yaml_"
FIXTURES_PFAD = WURZEL / "tests" / "fixtures"
AUFZEICHNUNG_ENTITY = "notify.pv_optimizer_aufzeichnung"

# Mittlere kW je Halbstunde, 08:00 bis 17:30 Serverzeit, aus einer echten
# Tageskurve. Davor und danach 0.
PV_KW_REAL = [0.35, 0.34, 0.90, 1.11, 1.77, 1.89, 1.79, 1.01, 1.07, 1.87,
              3.60, 2.78, 2.91, 1.80, 0.92, 1.88, 2.18, 2.15, 2.27, 1.91]
PV_REAL_START = dt.time(8, 0)


@pytest.fixture(scope="session")
def blueprint() -> dict:
    return lade_yaml(BLUEPRINT_PFAD)


@pytest.fixture(scope="session")
def package() -> dict:
    return lade_yaml(PACKAGE_PFAD)


# --------------------------------------------------------------------------
# Inputs: Defaults aus dem Blueprint, Entitaeten als feste Fake-IDs
# --------------------------------------------------------------------------
def fake_entity(name: str, domain: str) -> str:
    return f"{domain}.fx_{name}"


def standard_inputs(blueprint: dict) -> dict:
    inputs: dict = {}
    for name, definition in input_definitionen(blueprint).items():
        selector = definition.get("selector", {}) or {}
        if "entity" in selector:
            domain = selector["entity"].get("domain", "sensor")
            inputs[name] = fake_entity(name, domain)
        else:
            inputs[name] = definition.get("default")
    return inputs


# --------------------------------------------------------------------------
# Solcast-Prognose
# --------------------------------------------------------------------------
def prognose(tag: dt.date, kw_je_slot: list[float], start: dt.time, p10_anteil: float = 0.7) -> list[dict]:
    """detailedForecast fuer einen Tag: 48 Halbstunden, ausserhalb der Kurve 0."""
    slots = []
    for i in range(48):
        t = dt.datetime.combine(tag, dt.time(i // 2, 30 * (i % 2)), tzinfo=TZ)
        idx = (i - (start.hour * 2 + start.minute // 30))
        kw = kw_je_slot[idx] if 0 <= idx < len(kw_je_slot) else 0.0
        slots.append({
            "period_start": t,  # datetime, wie die Solcast-Integration es liefert
            "pv_estimate": round(kw, 4),
            "pv_estimate10": round(kw * p10_anteil, 4),
            "pv_estimate90": round(kw * 1.2, 4),
        })
    return slots


def prognose_real(tag: dt.date) -> list[dict]:
    return prognose(tag, PV_KW_REAL, PV_REAL_START)


def prognose_gleichmaessig(tag: dt.date, kw: float, von: dt.time = dt.time(8, 0), slots: int = 20) -> list[dict]:
    return prognose(tag, [kw] * slots, von)


# --------------------------------------------------------------------------
# Szenario-Bauer
# --------------------------------------------------------------------------
def szenario(
    blueprint: dict,
    jetzt: dt.datetime,
    *,
    soc: float = 84.0,
    forecast: list[dict] | None = None,
    profil_kwh: float | list[float] = 0.19,
    hausverbrauch_slot_kwh: float = 0.0,
    ladestrom: float = 200.0,
    boost_timer_idle_seit_s: float = 3600 * 6,
    zustands_overrides: dict[str, Zustand] | None = None,
    input_overrides: dict | None = None,
    trigger_id: str = "monitoring_5min",
) -> Harness:
    if jetzt.tzinfo is None:
        jetzt = jetzt.replace(tzinfo=TZ)
    inputs = standard_inputs(blueprint)
    # Zwei Packs, wie an beiden Standorten: die optionalen Pack-3-Inputs bleiben leer.
    inputs.update({"vmax3_sensor": "", "bms3_temp_min_sensor": "", "bms3_temp_max_sensor": ""})
    if input_overrides:
        inputs.update(input_overrides)
    e = lambda name: inputs[name]  # noqa: E731

    vor_1h = jetzt - dt.timedelta(hours=1)
    profil = [profil_kwh] * 48 if isinstance(profil_kwh, (int, float)) else list(profil_kwh)
    forecast = forecast if forecast is not None else prognose_real(jetzt.date())
    start = lambda s: s["period_start"] if isinstance(s["period_start"], dt.datetime) else dt.datetime.fromisoformat(s["period_start"])  # noqa: E731
    aktueller_slot = next((s for s in forecast
                           if start(s) <= jetzt < start(s) + dt.timedelta(minutes=30)), None)
    pv_jetzt_w = (aktueller_slot["pv_estimate"] * 1000) if aktueller_slot else 0.0
    naechste_h_kwh = sum(s["pv_estimate"] * 0.5 for s in forecast
                         if jetzt <= start(s) < jetzt + dt.timedelta(hours=1))
    rest_kwh = sum(s["pv_estimate"] * 0.5 for s in forecast
                   if start(s) >= jetzt)

    def z(name: str, state, attributes: dict | None = None, last_changed: dt.datetime | None = None) -> Zustand:
        return Zustand(e(name), str(state), attributes or {}, last_changed or vor_1h)

    zustaende: list[Zustand] = [
        # Batterie & BMS
        z("battery_soc_sensor", soc),
        z("battery_power_sensor", -1500),
        z("vmax1_sensor", 3.35), z("vmax2_sensor", 3.34), z("vmax3_sensor", "unavailable"),
        z("battery_total_charge", 1000.0), z("battery_total_discharge", 800.0),
        z("bms1_temp_min_sensor", 22.0), z("bms2_temp_min_sensor", 22.5), z("bms3_temp_min_sensor", "unavailable"),
        z("bms1_temp_max_sensor", 24.0), z("bms2_temp_max_sensor", 24.5), z("bms3_temp_max_sensor", "unavailable"),
        # Wechselrichter
        z("wr_max_charge_current", ladestrom, last_changed=jetzt - dt.timedelta(minutes=45)),
        z("wr_float_voltage_sensor", 53.6), z("wr_battery_voltage_sensor", 53.0),
        z("wr_grid_charge_switch", "off"), z("wr_grid_charge_current", 50),
        *[z(f"wr_tou_{i}", 20) for i in range(1, 7)],
        # Netz & PV
        z("grid_export_sensor", -500), z("grid_export_kwh_heute", 3.2),
        z("pv_power_sensor", pv_jetzt_w), z("pv_erzeugung_heute_sensor", 0.0),
        # Solcast
        z("solcast_heute_sensor", round(sum(s["pv_estimate"] * 0.5 for s in forecast), 2),
          {"detailedForecast": forecast}),
        z("solcast_rest_sensor", round(rest_kwh, 2)),
        z("solcast_current_sensor", pv_jetzt_w),
        z("solcast_next_hour_sensor", round(naechste_h_kwh, 3), {"estimate10": round(naechste_h_kwh * 0.7, 3)}),
        # Hausverbrauch
        z("hausverbrauch_json_text", json.dumps(profil)),
        z("hausverbrauch_utility_sensor", hausverbrauch_slot_kwh),
        # Waermepumpe
        z("wp_temp_sensor", 48.0), z("wp_water_heater", "heat", {"temperature": 50}),
        z("wp_kompressor_sensor", "off"), z("wp_ziel_temp_number", 50), z("wp_hysterese_number", 10),
        z("wp_boost_button", "unknown"),
        # Helfer
        z("helper_lade_modus", "normal"), z("helper_logbook_dummy", ""),
        z("helper_batterie_heute_voll", "off"), z("helper_blockade_beendet", "off"),
        z("helper_zwangsladung_aktiv", "off"), z("json_tracking_sensor", "[]"),
        z("helper_max_soc_heute", soc), z("speicherverlust_gesamt", 0.0), z("helper_offener_verlust", 0.0),
        z("helper_tare_charge", 990.0), z("helper_tare_discharge", 790.0),
        z("schatten_bms_sensor", soc, {"raw_drift_soc": soc}),
        z("eingriff_dauer_sensor", 0.0),
        # Timer
        z("helper_timer_peak", "idle"), z("helper_timer_cooldown", "idle"),
        z("helper_timer_wp_anlauf", "idle"),
        z("helper_timer_wp_boost", "idle", last_changed=jetzt - dt.timedelta(seconds=boost_timer_idle_seit_s)),
    ]
    tabelle = {zs.entity_id: zs for zs in zustaende}
    tabelle["sun.sun"] = Zustand("sun.sun", "above_horizon" if 6 <= jetzt.hour < 21 else "below_horizon")
    if zustands_overrides:
        tabelle.update(zustands_overrides)
    return Harness(blueprint, inputs, tabelle, jetzt, trigger_id)


# --------------------------------------------------------------------------
# Echte Laeufe aus der Diagnose-Aufzeichnung
# --------------------------------------------------------------------------
def aufzeichnung_lesen(zeile: str) -> dict:
    """Eine Zeile der Aufzeichnung; ein Zeitstempel-Praefix der File-Integration wird uebersprungen."""
    return json.loads(zeile[zeile.index("{"):])


def szenario_aus_aufzeichnung(blueprint: dict, aufz: dict) -> Harness:
    """
    Baut aus einer aufgezeichneten Zeile das Harness fuer genau diesen Lauf:
    Konfiguration wie an der Instanz, Entitaeten mit Zustand, last_changed und
    den Attributen, die der Blueprint liest; nicht aufgezeichnete Entity-Inputs
    waren an der Instanz leer.
    """
    inputs = standard_inputs(blueprint)
    inputs.update(aufz["konfiguration"])
    aufgezeichnet = {e["input"] for e in aufz["entitaeten"]}
    for name, d in input_definitionen(blueprint).items():
        if "entity" in (d.get("selector") or {}) and name not in aufgezeichnet:
            inputs[name] = ""
    tabelle: dict[str, Zustand] = {}
    for e in aufz["entitaeten"]:
        if e["last_changed"] is None:
            continue  # Entitaet existierte nicht: states() liefert 'unknown', states[...] None
        eid = inputs[e["input"]]
        tabelle[eid] = Zustand(eid, str(e["state"]), e.get("attributes") or {},
                               dt.datetime.fromisoformat(e["last_changed"]))
    tabelle["sun.sun"] = Zustand("sun.sun", aufz["sun"])
    return Harness(blueprint, inputs, tabelle, dt.datetime.fromisoformat(aufz["zeit"]), aufz["trigger"])


def zeit(tag: dt.date, hh: int, mm: int = 0) -> dt.datetime:
    return dt.datetime.combine(tag, dt.time(hh, mm), tzinfo=TZ)


@pytest.fixture(scope="session")
def tag() -> dt.date:
    return dt.date(2026, 8, 28)
