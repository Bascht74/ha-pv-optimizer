"""Das Vergleichswerkzeug baut aus einem Lauf eine gueltige Optimizer-Anfrage und liest den Fahrplan richtig."""
import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from optimizer_vergleich import SCHRITTE, SLOT, baue_anfrage, werte_antwort_aus  # noqa: E402
from conftest import szenario, zeit  # noqa: E402
from test_aufzeichnung import aufzeichnen  # noqa: E402


def test_anfrage_aus_einem_lauf(blueprint, tag):
    h = szenario(blueprint, zeit(tag, 21, 0), soc=60.0)
    aufz = aufzeichnen(h, blueprint)
    ctx = h.auswerten()
    a = baue_anfrage(ctx, aufz, preis_bezug=0.30, preis_einspeisung=0.08, restwert=0.30)
    ts, bat = a["time_series"], a["batteries"][0]
    assert len(ts["ft"]) == len(ts["gt"]) == len(ts["dt"]) == SCHRITTE and set(ts["dt"]) == {1800.0}
    # Wh-Skala: Kapazitaet 2 x 314 Ah x 51.2 V, Ladestand 60 %, Profil 0.19 kWh je Slot
    assert bat["s_capacity"] == pytest.approx(ctx["batterie_kapazitaet"] * 1000)
    assert bat["s_initial"] == pytest.approx(0.6 * bat["s_capacity"])
    assert bat["s_min"] == pytest.approx(ctx["default_tou_soc"] / 100 * bat["s_capacity"])
    assert set(ts["gt"]) == {190.0}
    # 21:00: heute keine PV mehr, morgen die Tagesform von heute auf die Summe von morgen skaliert
    assert all(f == 0 for f in ts["ft"][:6])          # 21:00 - 00:00
    assert sum(ts["ft"]) > 0
    morgen = next(float(e["state"]) for e in aufz["entitaeten"] if e["input"] == "solcast_morgen_sensor")
    assert sum(ts["ft"]) / 1000 == pytest.approx(morgen, rel=0.02)
    assert ts["p_N"][0] == pytest.approx(0.0003) and ts["p_E"][0] == pytest.approx(0.00008)


def test_fahrplan_wird_in_zeiten_und_nachtminimum_uebersetzt():
    start = dt.datetime(2026, 9, 9, 21, 0)
    kap = 32000.0
    ft = [0.0] * 20 + [1500.0] * 20 + [0.0] * 8           # Nacht bis 07:00, PV 07:00 - 17:00
    soc = [kap * 0.6] * 4 + [kap * 0.45] * 16 + [kap * 0.7] * 4 + [kap] * 24
    laden = [0.0] * 20 + [800.0] * 10 + [0.0] * 18
    anfrage = {"batteries": [{"s_capacity": kap}], "time_series": {"ft": ft}}
    antwort = {"status": "Optimal", "batteries": [{"state_of_charge": soc, "charging_power": laden}]}
    erg = werte_antwort_aus(antwort, anfrage, start)
    assert erg == {"laedt_ab": "07:00", "voll_um": "09:30", "nacht_min_soc": 45.0, "status": "Optimal"}
    assert (start + 20 * SLOT).strftime("%H:%M") == "07:00"
