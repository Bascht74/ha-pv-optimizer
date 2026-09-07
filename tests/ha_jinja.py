"""
Nachbau der Home-Assistant-Template-Umgebung, gerade gross genug, um die
variables:-Kette des Blueprints ausserhalb von Home Assistant auszuwerten.

Nachgebaut ist nur, was der Blueprint tatsaechlich benutzt:

- states(entity), states[entity].state / .last_changed / .attributes, states.domain.objekt
- state_attr, is_state, now, utcnow, today_at, as_datetime, as_local,
  as_timestamp, timedelta
- Filter float/int mit Default, round mit Methode (HA liefert bei
  precision 0 ein int), from_json, to_json, as_local, as_datetime, as_timestamp
- HA-"native types": ein gerendertes "[1, 2]" wird zur Liste, "5.0" zum float,
  "True" zum bool - dasselbe Verhalten, das der Blueprint an mehreren Stellen
  abfaengt ("HA liefert manchmal eine native Liste statt eines Strings")
- !input wird aus einem Dict aufgeloest, wie beim Blueprint-Import
- Die variables:-Schritte werden in Reihenfolge ausgewertet; jede Variable
  sieht alle vorigen - so rendert Home Assistant sie auch

Nicht nachgebaut: die Sandbox, das Undefined-Logging, Trigger-Templates.
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import math
import re
import zoneinfo
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jinja2
import jinja2.filters
import yaml

TZ = zoneinfo.ZoneInfo("Europe/Berlin")
_SENTINEL = object()
_NUMERISCH = re.compile(r"^-?\d+(\.\d+)?$")


# --------------------------------------------------------------------------
# YAML-Laden mit !input
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class InputTag:
    name: str


class _Loader(yaml.SafeLoader):
    pass


def _input_konstruktor(loader: yaml.Loader, node: yaml.Node) -> InputTag:
    return InputTag(loader.construct_scalar(node))


def _mapping_ohne_duplikate(loader: yaml.Loader, node: yaml.Node, deep: bool = False) -> dict:
    """yaml.SafeLoader ueberschreibt doppelte Keys stillschweigend - hier wird geworfen."""
    ergebnis: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in ergebnis:
            raise ValueError(f"Doppelter Key {key!r} in Zeile {key_node.start_mark.line + 1}")
        ergebnis[key] = loader.construct_object(value_node, deep=deep)
    return ergebnis


_Loader.add_constructor("!input", _input_konstruktor)
_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping_ohne_duplikate)


def lade_yaml(pfad: Path) -> Any:
    with open(pfad, encoding="utf-8") as f:
        return yaml.load(f, Loader=_Loader)


# --------------------------------------------------------------------------
# Zustandstabelle
# --------------------------------------------------------------------------
@dataclass
class Zustand:
    entity_id: str
    state: str
    attributes: dict = field(default_factory=dict)
    last_changed: dt.datetime | None = None

    @property
    def last_updated(self) -> dt.datetime | None:
        return self.last_changed


class States:
    """Callable UND subscriptable, wie in Home Assistant."""

    def __init__(self, tabelle: dict[str, Zustand]):
        self.tabelle = tabelle

    def __call__(self, entity_id: Any) -> str:
        if not entity_id or not isinstance(entity_id, str):
            return "unknown"
        z = self.tabelle.get(entity_id)
        return z.state if z is not None else "unknown"

    def __getitem__(self, entity_id: Any) -> Zustand | None:
        if not entity_id or not isinstance(entity_id, str):
            return None
        return self.tabelle.get(entity_id)

    def __getattr__(self, domain: str) -> "_DomainStates":
        # states.notify.xyz -> Zustand oder None, wie in Home Assistant.
        if domain.startswith("_"):
            raise AttributeError(domain)
        return _DomainStates(self.tabelle, domain)


class _DomainStates:
    def __init__(self, tabelle: dict[str, Zustand], domain: str):
        self._tabelle = tabelle
        self._domain = domain

    def __getattr__(self, name: str) -> Zustand | None:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._tabelle.get(f"{self._domain}.{name}")


# --------------------------------------------------------------------------
# HA-Filter und -Funktionen
# --------------------------------------------------------------------------
def ha_float(value: Any, default: Any = _SENTINEL) -> Any:
    try:
        return float(value)
    except (ValueError, TypeError):
        if default is _SENTINEL:
            raise ValueError(f"float(): kein Default und Wert {value!r} nicht wandelbar")
        return default


def ha_int(value: Any, default: Any = _SENTINEL, base: int = 10) -> Any:
    ergebnis = jinja2.filters.do_int(value, default=_SENTINEL, base=base)
    if ergebnis is _SENTINEL:
        if default is _SENTINEL:
            raise ValueError(f"int(): kein Default und Wert {value!r} nicht wandelbar")
        return default
    return ergebnis


def ha_round(value: Any, precision: int = 0, method: str = "common", default: Any = _SENTINEL) -> Any:
    try:
        multiplier = float(10 ** precision)
        v = float(value)
        if method == "ceil":
            v = math.ceil(v * multiplier) / multiplier
        elif method == "floor":
            v = math.floor(v * multiplier) / multiplier
        elif method == "half":
            v = round(v * 2) / 2
        else:
            v = round(v, precision)
        return int(v) if precision == 0 else v
    except (ValueError, TypeError):
        if default is _SENTINEL:
            raise ValueError(f"round(): kein Default und Wert {value!r} nicht wandelbar")
        return default


def ha_as_datetime(value: Any, default: Any = None) -> Any:
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time(), tzinfo=TZ)
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value, tz=TZ)
    if isinstance(value, str):
        s = value.strip().replace("Z", "+00:00")
        try:
            return dt.datetime.fromisoformat(s)
        except ValueError:
            return default
    return default


def ha_as_local(value: Any) -> Any:
    d = ha_as_datetime(value)
    if d is None:
        return value
    if d.tzinfo is None:
        d = d.replace(tzinfo=TZ)
    return d.astimezone(TZ)


def ha_as_timestamp(value: Any, default: Any = _SENTINEL) -> Any:
    d = ha_as_datetime(value)
    if d is None:
        if default is _SENTINEL:
            raise ValueError(f"as_timestamp(): {value!r} ist kein Zeitpunkt")
        return default
    if d.tzinfo is None:
        d = d.replace(tzinfo=TZ)
    return d.timestamp()


def parse_native(gerendert: str) -> Any:
    """HA _parse_result: Zahlen, Listen, Dicts, bool, None werden native, alles andere bleibt String."""
    s = gerendert.strip()
    if s == "":
        return s
    try:
        v = ast.literal_eval(s)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return s
    if isinstance(v, (str, complex)):
        return s
    if isinstance(v, (int, float)) and not isinstance(v, bool) and not _NUMERISCH.match(s):
        return s
    return v


# --------------------------------------------------------------------------
# Das Harness
# --------------------------------------------------------------------------
def _statische_env() -> jinja2.Environment:
    env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
    env.globals.update(
        as_datetime=ha_as_datetime, as_local=ha_as_local, as_timestamp=ha_as_timestamp,
        timedelta=dt.timedelta, float=ha_float, int=ha_int,
    )
    env.filters.update(
        float=ha_float, int=ha_int, round=ha_round, from_json=json.loads,
        # Wie HA: datetime-Objekte (period_start im Solcast-Attribut) sind NICHT
        # serialisierbar - to_json wirft dann "Type is not JSON serializable".
        to_json=lambda v, **kw: json.dumps(v, **kw),
        as_datetime=ha_as_datetime, as_local=ha_as_local, as_timestamp=ha_as_timestamp,
    )
    env.tests.update(
        number=lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        list=lambda v: isinstance(v, list),
        datetime=lambda v: isinstance(v, dt.datetime),
    )
    return env


# Eine Environment und ein Template-Cache fuer alle Harness-Instanzen: Das Kompilieren
# der ~240 Templates je Lauf kostete mehr als das Rendern. Zustand (states, now)
# kommt je Aufruf als Render-Kontext mit, nicht als Global.
_ENV = _statische_env()
_TEMPLATES: dict[str, jinja2.Template] = {}


class Harness:
    """Wertet die top-level variables:-Schritte des Blueprints in Reihenfolge aus."""

    def __init__(
        self,
        blueprint: dict,
        inputs: dict[str, Any],
        zustaende: dict[str, Zustand],
        jetzt: dt.datetime,
        trigger_id: str = "monitoring_5min",
    ):
        self.blueprint = blueprint
        self.inputs = inputs
        self.states = States(zustaende)
        if jetzt.tzinfo is None:
            jetzt = jetzt.replace(tzinfo=TZ)
        self.jetzt = jetzt.astimezone(TZ)
        self.trigger_id = trigger_id
        self._laufzeit = self._laufzeit_kontext()

    # -- Zustandsabhaengige Funktionen ------------------------------------
    def _laufzeit_kontext(self) -> dict:
        jetzt = self.jetzt
        states = self.states

        def now() -> dt.datetime:
            return jetzt

        def utcnow() -> dt.datetime:
            return jetzt.astimezone(dt.timezone.utc)

        def today_at(s: str = "00:00") -> dt.datetime:
            teile = [int(x) for x in str(s).split(":")]
            while len(teile) < 3:
                teile.append(0)
            return dt.datetime.combine(jetzt.date(), dt.time(*teile[:3]), tzinfo=TZ)

        def state_attr(entity_id: Any, attr: str) -> Any:
            z = states[entity_id]
            return z.attributes.get(attr) if z is not None else None

        def is_state(entity_id: Any, wert: Any) -> bool:
            return states(entity_id) == wert

        return dict(states=states, state_attr=state_attr, is_state=is_state,
                    now=now, utcnow=utcnow, today_at=today_at)

    # -- Rendern ----------------------------------------------------------
    def render(self, quelle: str, ctx: dict) -> Any:
        tpl = _TEMPLATES.get(quelle)
        if tpl is None:
            tpl = _ENV.from_string(quelle)
            _TEMPLATES[quelle] = tpl
        return parse_native(tpl.render(**self._laufzeit, **ctx))

    def _aufloesen(self, wert: Any, ctx: dict) -> Any:
        if isinstance(wert, InputTag):
            if wert.name not in self.inputs:
                raise KeyError(f"!input {wert.name} hat keinen Wert in der Fixture")
            return self.inputs[wert.name]
        if isinstance(wert, str):
            return self.render(wert, ctx) if ("{{" in wert or "{%" in wert) else wert
        if isinstance(wert, list):
            return [self._aufloesen(w, ctx) for w in wert]
        if isinstance(wert, dict):
            return {k: self._aufloesen(w, ctx) for k, w in wert.items()}
        return wert

    # -- Kette ------------------------------------------------------------
    def variablen_schritte(self) -> list[dict]:
        return [s["variables"] for s in self.blueprint["action"] if isinstance(s, dict) and "variables" in s]

    def auswerten(self, bis: str | None = None) -> dict[str, Any]:
        """Alle top-level Variablen in Reihenfolge; optional nach `bis` abbrechen."""
        ctx: dict[str, Any] = {"trigger": {"id": self.trigger_id}}
        for schritt in self.variablen_schritte():
            for name, roh in schritt.items():
                ctx[name] = self._aufloesen(roh, ctx)
                if name == bis:
                    return ctx
        if bis is not None:
            raise KeyError(f"Variable {bis!r} ist keine top-level Variable")
        return ctx


# --------------------------------------------------------------------------
# Hilfen fuer die Tests
# --------------------------------------------------------------------------
def alle_template_strings(knoten: Any, pfad: str = "") -> list[tuple[str, str]]:
    """Alle Strings mit Jinja-Klammern im Baum, mit Pfad - fuer die Klammerbilanz."""
    gefunden: list[tuple[str, str]] = []
    if isinstance(knoten, str):
        if "{{" in knoten or "{%" in knoten or "}}" in knoten or "%}" in knoten:
            gefunden.append((pfad, knoten))
    elif isinstance(knoten, dict):
        for k, v in knoten.items():
            gefunden.extend(alle_template_strings(v, f"{pfad}/{k}"))
    elif isinstance(knoten, list):
        for i, v in enumerate(knoten):
            gefunden.extend(alle_template_strings(v, f"{pfad}[{i}]"))
    return gefunden


def alle_input_tags(knoten: Any) -> set[str]:
    if isinstance(knoten, InputTag):
        return {knoten.name}
    if isinstance(knoten, dict):
        return set().union(*(alle_input_tags(v) for v in knoten.values())) if knoten else set()
    if isinstance(knoten, list):
        return set().union(*(alle_input_tags(v) for v in knoten)) if knoten else set()
    return set()


def input_definitionen(blueprint: dict) -> dict[str, dict]:
    """Flache Sicht auf alle Inputs, Sektionen aufgeloest."""
    flach: dict[str, dict] = {}
    for name, definition in blueprint["blueprint"]["input"].items():
        if isinstance(definition, dict) and "input" in definition:
            flach.update(definition["input"])
        else:
            flach[name] = definition
    return flach
