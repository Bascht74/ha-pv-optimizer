#!/usr/bin/env python3
"""
Zweitmeinung: aufgezeichnete Laeufe gegen den evcc-Optimizer rechnen.

Aus jeder Aufzeichnungszeile (art: lauf) werden die Eingaben des Optimizers gebaut
(Prognose je Slot, Verbrauchsprofil, Kapazitaet, Ladestand, Grenzen, feste Preise) und
an seine HTTP-Schnittstelle geschickt. Sein Batterie-Fahrplan wird neben die Werte
des Blueprints gestellt, die die Variablenkette fuer denselben Lauf liefert: wann er
laden wuerde und wann die Batterie voll waere gegen plan_voll_um / fallb_voll_um, das
naechtliche Ladestand-Minimum gegen die Untergrenze f_soc.

Der Optimizer braucht keine Verbrauchshistorie, sondern eine Verbrauchsprognose je
Slot: das ist das gelernte Profil, das auch der Blueprint benutzt. Beide sehen damit
denselben Bedarf; Unterschiede kommen aus der Planung, nicht aus den Daten.

    python3 tools/optimizer_vergleich.py tests/fixtures/dachterrasse_2026-09-04_bis_11.jsonl \\
        --url http://localhost:7050 --tag 2026-09-09 --stunden 07:00,13:00,21:00

Der Optimizer als Home-Assistant-Add-on lauscht auf Port 7050; mit JWT_TOKEN_SECRET
--token angeben. --dump schreibt Anfrage und Antwort je Lauf als JSON.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WURZEL / "tests"))

SLOT = dt.timedelta(minutes=30)
SCHRITTE = 48  # 24 Stunden Horizont in Halbstunden


def slot_index(t: dt.datetime) -> int:
    return t.hour * 2 + (1 if t.minute >= 30 else 0)


def baue_anfrage(ctx: dict, aufz: dict, *, preis_bezug: float, preis_einspeisung: float,
                 restwert: float, einspeiselimit_w: float | None = None) -> dict:
    """Optimizer-Eingabe aus den Variablen des Laufs (ctx) und der Zeile (aufz). Energie in Wh."""
    jetzt = dt.datetime.fromisoformat(aufz["zeit"])
    start = jetzt.replace(minute=0 if jetzt.minute < 30 else 30, second=0, microsecond=0)

    heute = [0.0] * 48
    for e in aufz["entitaeten"]:
        if e["input"] == "solcast_heute_sensor":
            for s in (e.get("attributes") or {}).get("detailedForecast") or []:
                t = dt.datetime.fromisoformat(s["period_start"]).astimezone(jetzt.tzinfo)
                if t.date() == jetzt.date():
                    heute[slot_index(t)] = float(s["pv_estimate"]) * 0.5  # kW mittel -> kWh
    heute_summe = sum(heute)
    morgen_kwh = next((float(e["state"]) for e in aufz["entitaeten"]
                       if e["input"] == "solcast_morgen_sensor" and str(e["state"]).replace('.', '', 1).isdigit()), None)
    # Morgen liegt nur als Tagessumme vor: Tagesform von heute, auf die Summe von morgen skaliert.
    skala_morgen = (morgen_kwh / heute_summe) if (morgen_kwh is not None and heute_summe > 0) else 1.0

    profil = [float(x) for x in ctx["json_profil"]]
    ft, gt, dts = [], [], []
    for k in range(SCHRITTE):
        t = start + k * SLOT
        idx = slot_index(t)
        skala = 1.0 if t.date() == jetzt.date() else skala_morgen
        ft.append(round(heute[idx] * skala * 1000, 1))
        gt.append(round(profil[idx] * 1000, 1))
        dts.append(SLOT.total_seconds())

    kap_wh = float(ctx["batterie_kapazitaet"]) * 1000
    p_max_w = float(ctx["max_ampere"]) * float(ctx["nominale_spannung"])
    anfrage = {
        "strategy": {"charging_strategy": "charge_before_export"},
        "batteries": [{
            "s_capacity": kap_wh,
            "s_min": float(ctx["default_tou_soc"]) / 100 * kap_wh,
            "s_max": kap_wh,
            "s_initial": float(ctx["aktueller_soc"]) / 100 * kap_wh,
            "c_min": 0.0, "c_max": p_max_w, "d_max": p_max_w,
            "p_a": restwert / 1000,
            "charge_from_grid": False, "discharge_to_grid": False,
        }],
        "time_series": {
            "dt": dts, "gt": gt, "ft": ft,
            "p_N": [preis_bezug / 1000] * SCHRITTE,
            "p_E": [preis_einspeisung / 1000] * SCHRITTE,
        },
        "eta_c": 0.95, "eta_d": 0.95,
    }
    if einspeiselimit_w:
        anfrage["grid"] = {"p_max_exp": einspeiselimit_w}
    return anfrage


def werte_antwort_aus(antwort: dict, anfrage: dict, start: dt.datetime) -> dict:
    """Aus dem Fahrplan: erster Ladeschritt, Vollzeit, Ladestand-Minimum der kommenden Nacht (in %)."""
    bat = antwort["batteries"][0]
    kap = anfrage["batteries"][0]["s_capacity"]
    soc = [s / kap * 100 for s in bat["state_of_charge"]]
    laden = bat["charging_power"]
    ft = anfrage["time_series"]["ft"]
    zeit = lambda k: (start + k * SLOT).strftime("%H:%M")  # noqa: E731

    laedt_ab = next((zeit(k) for k, w in enumerate(laden) if w > 1), None)
    voll_um = next((zeit(k + 1) for k, s in enumerate(soc) if s >= 99), None)
    # Nacht = erster zusammenhaengender Abschnitt ohne PV, der mindestens sechs Stunden lang ist.
    nacht_min = None
    k = 0
    while k < len(ft):
        if ft[k] <= 0:
            ende = k
            while ende < len(ft) and ft[ende] <= 0:
                ende += 1
            if ende - k >= 12:
                nacht_min = round(min(soc[k:ende]), 1)
                break
            k = ende
        else:
            k += 1
    return {"laedt_ab": laedt_ab, "voll_um": voll_um, "nacht_min_soc": nacht_min, "status": antwort.get("status")}


def frage_optimizer(url: str, anfrage: dict, token: str | None) -> dict:
    daten = json.dumps(anfrage).encode()
    req = urllib.request.Request(url.rstrip("/") + "/optimize/charge-schedule", data=daten,
                                 headers={"Content-Type": "application/json"}, method="POST")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("aufzeichnung", type=Path)
    ap.add_argument("--url", default="http://localhost:7050")
    ap.add_argument("--token", default=None, help="JWT, falls der Optimizer mit JWT_TOKEN_SECRET laeuft")
    ap.add_argument("--tag", default=None, help="nur Laeufe dieses Tages (YYYY-MM-DD)")
    ap.add_argument("--stunden", default="07:00,13:00,21:00", help="Laufzeiten HH:MM, kommagetrennt")
    ap.add_argument("--preis-bezug", type=float, default=0.30, help="EUR/kWh Netzbezug")
    ap.add_argument("--preis-einspeisung", type=float, default=0.08, help="EUR/kWh Einspeisung")
    ap.add_argument("--restwert", type=float, default=None, help="EUR/kWh fuer Energie in der Batterie am Horizontende (Standard: Bezugspreis)")
    ap.add_argument("--einspeiselimit", type=float, default=None, help="W, Einspeisegrenze des Wechselrichters")
    ap.add_argument("--dump", type=Path, default=None, help="Verzeichnis fuer Anfrage/Antwort je Lauf")
    args = ap.parse_args()

    import conftest as c
    from ha_jinja import lade_yaml
    bp = lade_yaml(str(WURZEL / "PV-Ladesteuerung.yaml"))
    stunden = {s.strip() for s in args.stunden.split(",")}
    restwert = args.restwert if args.restwert is not None else args.preis_bezug

    zeilen = [c.aufzeichnung_lesen(z) for _, z in c.aufzeichnungen(args.aufzeichnung)]
    print(f"{'Lauf':<17}{'SOC':>5}  {'Optimizer: lädt ab / voll um / Nacht-Min':<44}{'Blueprint: plan voll / voller Strom / Untergrenze':<50}")
    for a in zeilen:
        if a.get("art") not in (None, "lauf"):
            continue
        t = dt.datetime.fromisoformat(a["zeit"])
        if args.tag and t.date().isoformat() != args.tag:
            continue
        if t.minute % 30 >= 5 or t.replace(minute=t.minute - t.minute % 30).strftime("%H:%M") not in stunden:
            continue
        ctx = c.szenario_aus_aufzeichnung(bp, a).auswerten()
        anfrage = baue_anfrage(ctx, a, preis_bezug=args.preis_bezug, preis_einspeisung=args.preis_einspeisung,
                               restwert=restwert, einspeiselimit_w=args.einspeiselimit)
        try:
            antwort = frage_optimizer(args.url, anfrage, args.token)
        except urllib.error.URLError as e:
            print(f"{t:%d.%m. %H:%M}  Optimizer nicht erreichbar: {e}")
            return 1
        start = t.replace(minute=0 if t.minute < 30 else 30, second=0, microsecond=0)
        erg = werte_antwort_aus(antwort, anfrage, start)
        if args.dump:
            args.dump.mkdir(parents=True, exist_ok=True)
            (args.dump / f"{t:%Y-%m-%d_%H%M}.json").write_text(json.dumps({"anfrage": anfrage, "antwort": antwort, "blueprint": {
                k: ctx.get(k) for k in ("plan_voll_um", "fallb_voll_um", "f_soc", "tou_ist", "untergrenze_um", "aktueller_soc")}}, indent=1))
        opt = f"{erg['laedt_ab'] or '—':<6} / {erg['voll_um'] or 'nicht im Horizont':<18} / {erg['nacht_min_soc'] if erg['nacht_min_soc'] is not None else '—'} %"
        bpw = f"{ctx.get('plan_voll_um', '')} / {ctx.get('fallb_voll_um', '')} / {ctx.get('f_soc')} % ({ctx.get('untergrenze_um') or 'tags keine Schätzung'})"
        print(f"{t:%d.%m. %H:%M}  {ctx['aktueller_soc']:>4.0f}  {opt:<44}{bpw}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
