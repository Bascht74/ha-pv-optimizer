"""
Welcher Zweig der Prioritaetskaskade gewinnt - und was er schreibt.

Das Harness in ha_jinja.py rendert die Variablenkette. Hier kommt die Stufe
darueber dazu: die Auswahl im grossen choose: und die Aktionen des gewinnenden
Zweigs. Absichtlich nur die Bedingungs- und Schrittarten, die der Blueprint
heute benutzt - eine neue Art laesst die Tests scheitern, statt still als
'trifft nicht zu' durchzugehen.

Nicht nachgebildet ist alles Zeitliche: mode: queued, delay, repeat, zwei
gleichzeitige Laeufe, der Mindestabstand der Trigger. Der Ausfuehrer aendert
die Zustandstabelle nicht, ein spaeterer Schritt sieht also nicht, was ein
frueherer geschrieben hat.
"""
from __future__ import annotations

from typing import Any

from ha_jinja import Harness


class UnbekannteArt(AssertionError):
    """Der Blueprint benutzt etwas, das dieser Ausfuehrer nicht kennt."""


class Stopp(Exception):
    """Ein stop:-Schritt hat die Sequenz beendet."""


def kaskade(blueprint: dict) -> list[dict]:
    """Die Zweige der Master-Kaskade, erkannt am Alias des ersten Zweigs."""
    for schritt in blueprint["action"]:
        zweige = schritt.get("choose") if isinstance(schritt, dict) else None
        if zweige and str(zweige[0].get("alias", "")).startswith("PRIO 0"):
            return zweige
    raise AssertionError("Master-Kaskade nicht gefunden")


def bedingung(h: Harness, c: Any, ctx: dict) -> bool:
    """Eine einzelne Bedingung auswerten."""
    if isinstance(c, str):  # Kurzform: nacktes Template
        return bool(h.render(c, ctx))
    art = c.get("condition")
    if art == "template":
        return bool(h.render(c["value_template"], ctx))
    if art == "trigger":
        ids = c["id"] if isinstance(c["id"], list) else [c["id"]]
        return h.trigger_id in [str(i) for i in ids]
    if art == "state":
        soll = h._aufloesen(c["state"], ctx)
        werte = soll if isinstance(soll, list) else [soll]
        return h.states(h._aufloesen(c["entity_id"], ctx)) in [str(w) for w in werte]
    if art == "and":
        return all(bedingung(h, x, ctx) for x in c["conditions"])
    if art == "or":
        return any(bedingung(h, x, ctx) for x in c["conditions"])
    if art == "not":
        return not any(bedingung(h, x, ctx) for x in c["conditions"])
    raise UnbekannteArt(f"Bedingungsart {art!r} ist hier nicht nachgebildet")


def _alle(h: Harness, bedingungen: Any, ctx: dict) -> bool:
    if bedingungen is None:
        return True
    if not isinstance(bedingungen, list):
        bedingungen = [bedingungen]
    return all(bedingung(h, c, ctx) for c in bedingungen)


def _lauf(h: Harness, schritte: list, ctx: dict, aktionen: list) -> None:
    """Die Sequenz eines Zweigs abspielen und die Serviceaufrufe sammeln."""
    for s in schritte:
        if "variables" in s:
            ctx = dict(ctx)
            for name, roh in s["variables"].items():  # wie HA: jede sieht die vorigen
                ctx[name] = h._aufloesen(roh, ctx)
        elif "stop" in s:
            aktionen.append(("stop", None, h._aufloesen(s["stop"], ctx)))
            raise Stopp
        elif "if" in s:
            zweig = s.get("then") if _alle(h, s["if"], ctx) else s.get("else")
            _lauf(h, zweig or [], ctx, aktionen)
        elif "choose" in s:
            gewaehlt = next((o["sequence"] for o in s["choose"]
                             if _alle(h, o.get("conditions"), ctx)), s.get("default"))
            _lauf(h, gewaehlt or [], ctx, aktionen)
        elif "action" in s:
            daten = h._aufloesen(s.get("data") or {}, ctx)
            ziel = h._aufloesen((s.get("target") or {}).get("entity_id"), ctx)
            wert = next((daten[k] for k in ("value", "option", "duration", "message") if k in daten), None)
            aktionen.append((s["action"], ziel, wert))
        elif "delay" in s or "repeat" in s or "wait_template" in s:
            raise UnbekannteArt(f"Schrittart {sorted(s)[0]!r} ist hier nicht nachgebildet")
        else:
            raise UnbekannteArt(f"Schrittart {sorted(s)} ist hier nicht nachgebildet")


def zweig_und_aktionen(h: Harness, blueprint: dict, ctx: dict | None = None) -> tuple[str | None, list]:
    """(Alias des gewinnenden Zweigs, [(service, entity_id, wert), ...])."""
    ctx = h.auswerten() if ctx is None else ctx
    for z in kaskade(blueprint):
        if _alle(h, z["conditions"], ctx):
            aktionen: list = []
            try:
                _lauf(h, z["sequence"], ctx, aktionen)
            except Stopp:
                pass
            return z["alias"], aktionen
    return None, []


def zweig(h: Harness, blueprint: dict, ctx: dict | None = None) -> str | None:
    """Nur die Prioritaet des gewinnenden Zweigs, z. B. 'PRIO 5'."""
    alias = zweig_und_aktionen(h, blueprint, ctx)[0]
    return alias.split(":")[0] if alias else None


def schreibt(aktionen: list, service: str = "number.set_value") -> list:
    """Die Werte, die ein Service bekommen hat."""
    return [w for s, _, w in aktionen if s == service]
