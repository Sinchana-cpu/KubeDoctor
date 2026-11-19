"""Runbook knowledge base: load rules and score them against collected evidence."""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .safety import redact


@dataclass
class Match:
    label: str
    source: str
    line: str
    weight: int


@dataclass
class Score:
    rule: dict
    got: int = 0
    total: int = 0
    matches: list = field(default_factory=list)
    margin: int = 0

    @property
    def ratio(self):
        return self.got / self.total if self.total else 0.0

    @property
    def confidence(self):
        if self.got == 0:
            return "None"
        level = "Low"
        if self.ratio >= 0.75 and self.margin >= 2:
            level = "High"
        elif self.ratio >= 0.5:
            level = "Medium"
        if self.rule.get("fallback") and level == "High":
            level = "Medium"      # "it crashes" is known, "why" is not
        return level


def load_rules(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    rules = data["rules"]
    for r in rules:                       # fail early on a broken runbook
        for key in ("id", "title", "symptoms", "signals", "plain_language", "fix"):
            if key not in r:
                raise ValueError(f"Runbook rule {r.get('id', '?')} is missing '{key}'")
        for s in r["signals"]:
            re.compile(s["pattern"])
    return rules


def _find_line(text, regex):
    m = regex.search(text)
    if not m:
        return None
    start = text.rfind("\n", 0, m.start()) + 1
    end = text.find("\n", m.end())
    return redact(text[start:end if end != -1 else len(text)].strip())[:220]


def score_rule(rule, evidence):
    s = Score(rule=rule)
    for sig in rule["signals"]:
        s.total += sig["weight"]
        parts = sig["source"].split("|")
        text = "\n".join(evidence.get(p, "") for p in parts)
        rx = re.compile(sig["pattern"], re.I | re.M)
        line = _find_line(text, rx)
        if line is not None:
            s.got += sig["weight"]
            s.matches.append(Match(sig["label"], parts[0] if len(parts) == 1 else "events", line, sig["weight"]))
    return s


def rank(rules, symptom, evidence):
    """Score every rule that applies to ``symptom``; best first. Fallback rules lose ties."""
    scored = [score_rule(r, evidence) for r in rules if symptom in r["symptoms"]]
    scored.sort(key=lambda s: (s.got, not s.rule.get("fallback", False), s.ratio), reverse=True)
    for i, s in enumerate(scored):
        nxt = scored[i + 1].got if i + 1 < len(scored) else 0
        s.margin = s.got - nxt if i == 0 else 0
    return scored
