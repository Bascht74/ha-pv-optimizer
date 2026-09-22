"""
Welcher Zweig einer choose:-Gruppe gewinnt - und was er schreibt.

Das Harness in ha_jinja.py rendert die Variablenkette. Hier kommt die Stufe
darueber dazu: die Auswahl in den choose:-Gruppen auf oberster Ebene - der
Prioritaetskaskade und den Gruppen daneben - und die Aktionen des gewinnenden
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


def gruppen(blueprint: dict) -> list[list[dict]]:
    """Alle choose:-Gruppen auf oberster Ebene, in der Reihenfolge des Blueprints."""
    return [s["choose"] for s in blueprint["action"]
            if isinstance(s, dict) and s.get("choose")]


def gruppe(blueprint: dict, alias_anfang: str) -> list[dict]:
    """
    Die choose:-Gruppe, die einen Zweig mit diesem Alias-Anfang enthaelt.

    Mehrdeutig ist ein Fehler, kein Zufallstreffer: Passt der Anfang auf Zweige
    zweier Gruppen, wuerde ein Szenario still gegen die falsche geprueft.
    """
    treffer = [z for z in gruppen(blueprint)
               if any(str(x.get("alias", "")).startswith(alias_anfang) for x in z)]
    if not treffer:
        raise AssertionError(f"choose-Gruppe zu {alias_anfang!r} nicht gefunden")
    if len(treffer) > 1:
        raise AssertionError(f"Alias-Anfang {alias_anfang!r} passt auf {len(treffer)} Gruppen")
    return treffer[0]


def kaskade(blueprint: dict) -> list[dict]:
    """Die Zweige der Master-Kaskade."""
    return gruppe(blueprint, "PRIO 0")


def template_wahr(h: Harness, quelle: str, ctx: dict) -> bool:
    """
    Eine Template-Bedingung wie Home Assistant auswerten.

    HA vergleicht den gerenderten TEXT mit 'true' - alles andere ist falsch, auch
    ein nicht leerer String. Python-Wahrheit ist hier der falsche Massstab: Eine
    Variable, die ihren Wert als Text traegt ('false' aus einem {% else %}-Zweig),
    ist fuer bool() wahr und fuer HA falsch. Booleans kommen aus der Kette schon
    als bool und bleiben, wie sie sind.
    """
    wert = h.render(quelle, ctx)
    if isinstance(wert, bool):
        return wert
    return str(wert).strip().lower() == "true"


def bedingung(h: Harness, c: Any, ctx: dict) -> bool:
    """Eine einzelne Bedingung auswerten."""
    if isinstance(c, str):  # Kurzform: nacktes Template
        return template_wahr(h, c, ctx)
    art = c.get("condition")
    if art == "template":
        return template_wahr(h, c["value_template"], ctx)
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
            wert = next((daten[k] for k in ("value", "option", "duration", "temperature", "message")
                         if k in daten), None)
            aktionen.append((s["action"], ziel, wert))
        elif "delay" in s or "repeat" in s or "wait_template" in s:
            raise UnbekannteArt(f"Schrittart {sorted(s)[0]!r} ist hier nicht nachgebildet")
        else:
            raise UnbekannteArt(f"Schrittart {sorted(s)} ist hier nicht nachgebildet")


def gewinner(h: Harness, zweige: list[dict], ctx: dict | None = None) -> dict | None:
    """
    Der erste Zweig, dessen Bedingungen zutreffen - ohne seine Sequenz zu spielen.

    Getrennt von zweig_und_aktionen, weil die Abdeckung nur die Auswahl braucht:
    Einige Zweige ausserhalb der Kaskade benutzen repeat:/delay:, die der
    Ausfuehrer bewusst verweigert, statt sie falsch nachzubilden.
    """
    ctx = h.auswerten() if ctx is None else ctx
    return next((z for z in zweige if _alle(h, z["conditions"], ctx)), None)


def zweig_und_aktionen(h: Harness, blueprint: dict, ctx: dict | None = None,
                       zweige: list[dict] | None = None) -> tuple[str | None, list]:
    """(Alias des gewinnenden Zweigs, [(service, entity_id, wert), ...])."""
    ctx = h.auswerten() if ctx is None else ctx
    z = gewinner(h, zweige if zweige is not None else kaskade(blueprint), ctx)
    if z is None:
        return None, []
    aktionen: list = []
    try:
        _lauf(h, z["sequence"], ctx, aktionen)
    except Stopp:
        pass
    return z["alias"], aktionen


def zweig(h: Harness, blueprint: dict, ctx: dict | None = None) -> str | None:
    """Nur die Prioritaet des gewinnenden Zweigs, z. B. 'PRIO 5'."""
    alias = zweig_und_aktionen(h, blueprint, ctx)[0]
    return alias.split(":")[0] if alias else None


def schreibt(aktionen: list, service: str = "number.set_value", ziel: str | None = None) -> list:
    """Die Werte, die ein Service bekommen hat; mit ziel nur die an diese Entitaet."""
    return [w for s, e, w in aktionen
            if s == service and (ziel is None or ziel == e or (isinstance(e, list) and ziel in e))]
