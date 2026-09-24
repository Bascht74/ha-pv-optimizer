"""
Fixtures fuer die Blueprint-Tests.

Die Szenarien sind synthetisch, aber in realistischen Wertebereichen: die
PV-Kurve ist die geglaettete Erzeugung eines echten Tages an einem der beiden
Standorte, die Batterie hat 2 x 314 Ah bei 51,2 V (32,2 kWh), das
Verbrauchsprofil liegt bei 0,19 kWh je Halbstunde.

Echte Laeufe kommen aus der Diagnose-Aufzeichnung des Blueprints (JSON-Zeilen
an notify.pv_optimizer_aufzeichnung, Einrichtung im Package-Kopf). Solche
Dateien sind private Standortdaten: Sie liegen nur lokal unter
tests/fixtures/*.jsonl (per .gitignore ausgeschlossen), die Tests darauf
ueberspringen sich ohne Datei. szenario_aus_aufzeichnung baut aus jeder Zeile
ein Harness, die geloggten Entscheidungen des Tages sind der Erwartungswert.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import warnings
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
            eid = fake_entity(name, domain)
            inputs[name] = [eid] if selector["entity"].get("multiple") else eid
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
    schatten_soc: float | str | None = None,
    forecast: list[dict] | None = None,
    profil_kwh: float | list[float] = 0.19,
    hausverbrauch_slot_kwh: float = 0.0,
    ladestrom: float = 200.0,
    boost_timer_idle_seit_s: float = 3600 * 6,
    prognose_tage_kwh: tuple[float, float, float] | None = (25.0, 20.0, 15.0),
    prognose_tage_p10_anteil: float = 1.0,
    zustands_overrides: dict[str, Zustand] | None = None,
    input_overrides: dict | None = None,
    trigger_id: str = "monitoring_5min",
    integrationen: dict[str, str] | None = None,
) -> Harness:
    if jetzt.tzinfo is None:
        jetzt = jetzt.replace(tzinfo=TZ)
    inputs = standard_inputs(blueprint)
    # Zwei Packs, wie an beiden Standorten: die optionalen Pack-3-Inputs bleiben leer.
    inputs.update({"vmax3_sensor": "", "bms3_temp_min_sensor": "", "bms3_temp_max_sensor": ""})
    # Schatten-BMS: ohne Angabe nicht zugewiesen - der Standardfall, in dem der
    # Blueprint allein mit dem Ladestand des Wechselrichters rechnet.
    if schatten_soc is None:
        inputs["schatten_soc_sensor"] = ""
    # Entlade-Planung: Tagesprognosen morgen / Tag 3 / Tag 4; None schaltet sie aus.
    if prognose_tage_kwh is None:
        inputs.update({"solcast_morgen_sensor": "", "solcast_tag3_sensor": "", "solcast_tag4_sensor": ""})
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
        z("battery_total_charge", 1000.0),
        z("bms1_temp_min_sensor", 22.0), z("bms2_temp_min_sensor", 22.5), z("bms3_temp_min_sensor", "unavailable"),
        z("bms1_temp_max_sensor", 24.0), z("bms2_temp_max_sensor", 24.5), z("bms3_temp_max_sensor", "unavailable"),
        # Wechselrichter
        z("wr_max_charge_current", ladestrom, last_changed=jetzt - dt.timedelta(minutes=45)),
        z("wr_battery_voltage_sensor", 53.0),
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
        *[z(name, kwh, {"estimate10": round(kwh * prognose_tage_p10_anteil, 3)})
          for name, kwh in zip(("solcast_morgen_sensor", "solcast_tag3_sensor", "solcast_tag4_sensor"), prognose_tage_kwh or ())],
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
        z("json_tracking_sensor", "[]"),
        z("helper_max_soc_heute", soc), z("speicherverlust_gesamt", 0.0), z("helper_offener_verlust", 0.0),
        z("helper_halten_bezug", 0.0),
        z("eingriff_dauer_sensor", 0.0),
        # Timer
        z("helper_timer_peak", "idle"), z("helper_timer_cooldown", "idle"),
        z("helper_timer_wp_anlauf", "idle"),
        z("helper_timer_wp_boost", "idle", last_changed=jetzt - dt.timedelta(seconds=boost_timer_idle_seit_s)),
    ]
    tabelle = {zs.entity_id: zs for zs in zustaende}
    if schatten_soc is not None:
        tabelle[inputs["schatten_soc_sensor"]] = Zustand(
            inputs["schatten_soc_sensor"], str(schatten_soc), {}, vor_1h)
    tabelle["sun.sun"] = Zustand("sun.sun", "above_horizon" if 6 <= jetzt.hour < 21 else "below_horizon")
    if zustands_overrides:
        tabelle.update(zustands_overrides)
    return Harness(blueprint, inputs, tabelle, jetzt, trigger_id, integrationen)


# --------------------------------------------------------------------------
# Referenzrechnung der Entlade-Planung (unabhaengig vom Jinja des Blueprints)
# --------------------------------------------------------------------------
def plan_referenz(jetzt: dt.datetime, tage: dict, profil: list[float], *, kap_kwh: float,
                  v3: float = 0.7, v4: float = 0.5, eta: float = 0.92, ziel: float = 90.0,
                  reserve: float = 50.0, minimum: float = 20.0, soc: float | None = None,
                  notstrom_pct: float = 0.0) -> dict:
    """
    tage: Kalenderdatum -> 48 Halbstundenwerte kWh (bereits P50/P10-gemischt).
    Rollierend ab dem laufenden Slot 144 Halbstunden: Ueberschuss je Slot mal Gewicht
    nach Vorlauf (1.0 / v3 / v4 fuer < 24 h / < 48 h / darueber), Defizite voll;
    Summe bei null geklemmt (Defizite vor einer Sonnenstrecke traegt die Untergrenze);
    B* = groesstes Zwischenmaximum; Start = erster Slot mit mehr als 0.01 kWh Summe.
    notstrom_pct: Notstromreserve in Prozent (reserve_referenz), auf 5 % aufgerundet als Mindestwert.
    """
    start_idx = jetzt.hour * 2 + (1 if jetzt.minute >= 30 else 0)
    kum = best = 0.0
    start = None
    for k in range(144):
        g = start_idx + k
        d, idx = divmod(g, 48)
        pv = tage.get(jetzt.date() + dt.timedelta(days=d), [0.0] * 48)[idx]
        w = 1.0 if k < 48 else (v3 if k < 96 else v4)
        n = pv * eta - profil[idx]
        b = n * w if n > 0 else n
        kum = max(kum + b, 0.0)
        best = max(best, kum)
        if start is None and kum > 0.01:
            start = jetzt.replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(minutes=30 * g)
    b_pct = best / kap_kwh * 100
    f_roh = max(ziel - b_pct, min(reserve, 100 - b_pct), math.ceil(notstrom_pct / 5) * 5)
    plan = (f_roh // 5) * 5
    wert = plan if (soc is None or f_roh <= soc) else float(int(soc))
    f_soc = int(min(max(wert, minimum), 100))
    return {"b_kwh": best, "b_pct": b_pct, "f_roh": f_roh, "f_soc": f_soc, "start": start}


def reserve_referenz(jetzt: dt.datetime, tage10: dict, profil_wh: list[float], *, kap_kwh: float,
                     shutdown: float = 10.0, eigen_kwh: float = 0.045, eta_pv: float = 0.92,
                     eta_ac: float = 0.95, horizont_h: float = 48) -> dict:
    """
    tage10: Kalenderdatum -> 48 P10-Halbstundenwerte kWh. Ab dem laufenden Slot horizont_h x 2 Halbstunden:
    Last (Profil in Wh plus Eigenverbrauch, durch den Entlade-Wirkungsgrad) minus P10-PV mal
    Ladewirkungsgrad, kumuliert ohne Klemmung; Reserve = Abschalt-Ladestand plus groesstes
    Zwischenmaximum, bis = Ende des Slots, in dem es erreicht wird.
    """
    start_idx = jetzt.hour * 2 + (1 if jetzt.minute >= 30 else 0)
    kum = best = 0.0
    bis = None
    for k in range(round(horizont_h * 2)):
        g = start_idx + k
        d, idx = divmod(g, 48)
        pv = tage10.get(jetzt.date() + dt.timedelta(days=d), [0.0] * 48)[idx]
        kum += (profil_wh[idx] / 1000 + eigen_kwh) / eta_ac - pv * eta_pv
        if kum > best:
            best = kum
            bis = jetzt.replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(minutes=30 * (g + 1))
    return {"kwh": best, "pct": shutdown + best / kap_kwh * 100, "bis": bis}


def tage_aus_szenario(jetzt: dt.datetime, prognose_tage_kwh, *, forecast: list[dict] | None = None,
                      p10_anteil: float = 1.0, a: float = 0.5) -> dict:
    """Dieselben Eingaben wie szenario(): heute aus dem Slot-Array, Folgetage in heutiger Form."""
    forecast = forecast if forecast is not None else prognose_real(jetzt.date())
    heute = [0.0] * 48
    for s in forecast:
        t = s["period_start"] if isinstance(s["period_start"], dt.datetime) else dt.datetime.fromisoformat(s["period_start"])
        heute[t.hour * 2 + (1 if t.minute >= 30 else 0)] += (s["pv_estimate"] * a + s["pv_estimate10"] * (1 - a)) * 0.5
    summe = sum(heute)
    tage = {jetzt.date(): heute}
    for i, kwh in enumerate(prognose_tage_kwh or (), start=1):
        blend = kwh * a + round(kwh * p10_anteil, 3) * (1 - a)
        tage[jetzt.date() + dt.timedelta(days=i)] = [v * blend / summe for v in heute] if summe > 0 else [0.0] * 48
    return tage


# --------------------------------------------------------------------------
# Echte Laeufe aus der Diagnose-Aufzeichnung
# --------------------------------------------------------------------------
def ist_aufzeichnung(zeile: str) -> bool:
    """Die File-Integration schreibt beim Anlegen zwei Kopfzeilen; dazu kommen Testsendungen."""
    return '"entitaeten"' in zeile and '"konfiguration"' in zeile


def aufzeichnung_lesen(zeile: str) -> dict:
    """Eine Zeile der Aufzeichnung; ein Zeitstempel-Praefix der File-Integration wird uebersprungen."""
    return json.loads(zeile[zeile.index("{"):])


def aufzeichnung_lesbar(zeile: str) -> bool:
    """
    Schreiben zwei Laeufe gleichzeitig, stehen zwei Aufzeichnungen ineinander in einer
    Zeile: Das JSON bricht mitten im Text ab, oder es parst und traegt die Pflichtfelder
    nicht. Beides ist eine zerrissene Zeile, kein Lauf.
    """
    try:
        d = aufzeichnung_lesen(zeile)
    except ValueError:
        return False
    return (isinstance(d, dict) and isinstance(d.get("zeit"), str)
            and isinstance(d.get("entitaeten"), list) and isinstance(d.get("konfiguration"), dict))


def zerrissene_zeilen(pfad: Path) -> list[int]:
    """Zeilennummern der Aufzeichnungen einer Datei, die nicht lesbar sind."""
    with open(pfad, encoding="utf-8") as f:
        return [nr for nr, z in enumerate(f, 1) if ist_aufzeichnung(z) and not aufzeichnung_lesbar(z)]


def aufzeichnungen(pfad: Path) -> list[tuple[int, str]]:
    """(Zeilennummer, Zeile) aller lesbaren Aufzeichnungen einer Datei; zerrissene Zeilen
    werden uebersprungen und mit ihren Nummern als Warnung gemeldet."""
    with open(pfad, encoding="utf-8") as f:
        kandidaten = [(nr, z) for nr, z in enumerate(f, 1) if ist_aufzeichnung(z)]
    lesbar = [(nr, z) for nr, z in kandidaten if aufzeichnung_lesbar(z)]
    kaputt = sorted(set(nr for nr, _ in kandidaten) - set(nr for nr, _ in lesbar))
    if kaputt:
        warnings.warn(f"{pfad.name}: {len(kaputt)} zerrissene Zeile(n) uebersprungen: {kaputt}", stacklevel=2)
    return lesbar


def szenario_aus_aufzeichnung(blueprint: dict, aufz: dict) -> Harness:
    """
    Baut aus einer aufgezeichneten Zeile das Harness fuer genau diesen Lauf:
    Konfiguration wie an der Instanz, Entitaeten mit Zustand, last_changed und
    den Attributen, die der Blueprint liest; nicht aufgezeichnete Entity-Inputs
    waren an der Instanz leer.
    """
    inputs = standard_inputs(blueprint)
    # Aufzeichnungen aelterer Versionen tragen Felder, die es nicht mehr gibt.
    definiert = input_definitionen(blueprint)
    inputs.update({k: v for k, v in aufz["konfiguration"].items() if k in definiert})
    aufgezeichnet = {e["input"] for e in aufz["entitaeten"] if e["input"] in definiert}
    mehrfach = {name for name, d in definiert.items()
                if (d.get("selector") or {}).get("entity", {}).get("multiple")}
    for name, d in definiert.items():
        if "entity" in (d.get("selector") or {}) and name not in aufgezeichnet:
            inputs[name] = [] if name in mehrfach else ""
    # Mehrfachauswahl: je aufgezeichnetem Eintrag eine eigene Fake-Entitaet.
    for name in mehrfach & aufgezeichnet:
        basis = inputs[name][0]
        inputs[name] = [basis if i == 0 else f"{basis}_{i}" for i, _ in enumerate(e for e in aufz["entitaeten"] if e["input"] == name)]
    zaehler: dict[str, int] = {}
    tabelle: dict[str, Zustand] = {}
    for e in aufz["entitaeten"]:
        if e["last_changed"] is None or e["input"] not in definiert:
            continue  # Entitaet existierte nicht (states() -> 'unknown') oder Input entfernt
        if e["input"] in mehrfach:
            eid = inputs[e["input"]][zaehler.get(e["input"], 0)]
            zaehler[e["input"]] = zaehler.get(e["input"], 0) + 1
        else:
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
