"""Explicit commands work without a model; optional models expand the vocabulary."""
from __future__ import annotations

import re
from .providers import Decider, ProviderError, ollama_plan


class PlanError(ValueError):
    pass


def parse_command(text: str, config: dict) -> tuple[str, list[dict]]:
    text = text.strip()
    match = re.fullmatch(r"(?:routine|start routine|run routine)\s+(.+)", text, re.I)
    if match:
        name = match[1].strip().casefold()
        scenes = config.get("scenes", {})
        key = next((key for key in scenes if key.casefold() == name), None)
        if key is None:
            raise PlanError("Unknown routine. Try: " + ", ".join(scenes))
        scene = scenes[key]
        return "routine", scene["steps"]
    match = re.fullmatch(r"(?:open|launch)\s+(.+)", text, re.I)
    if match:
        target = match[1].strip()
        kind, args = ("open_url", {"url": target}) if re.match(r"https?://", target, re.I) else ("open_app", {"app": target})
    elif match := re.fullmatch(r"(?:search(?: the web)? for|search)\s+(.+)", text, re.I | re.S):
        kind, args = "search_web", {"query": match[1].strip()}
    elif match := re.fullmatch(r"(?:message|text)\s+([^:]+):\s*(.+)", text, re.I | re.S):
        kind, args = "send_message", {"contact": match[1].strip(), "body": match[2]}
    elif match := re.fullmatch(r"(?:note|remember)\s+(.+)", text, re.I | re.S):
        title, separator, content = match[1].partition(":")
        kind, args = "create_note", {"title": title.strip() if separator else "Quick note",
                                      "content": content.strip() if separator else title}
    elif match := re.fullmatch(r"(?:set (?:a )?)?timer(?: for)?\s+(\d+)\s*(seconds?|minutes?|hours?)(?:\s+(?:called|named)\s+(.+))?", text, re.I):
        scale = {"s": 1, "m": 60, "h": 3600}[match[2][0].lower()]
        kind, args = "timer", {"seconds": int(match[1]) * scale, "label": match[3] or "Focus timer"}
    elif match := re.fullmatch(r"(?:set )?volume(?: to)?\s+(\d+)\s*%?", text, re.I):
        kind, args = "set_volume", {"level": int(match[1])}
    elif match := re.fullmatch(r"(?:say|read aloud)\s+(.+)", text, re.I | re.S):
        kind, args = "say", {"text": match[1]}
    elif match := re.fullmatch(r"(?:run )?shortcut\s+(.+)", text, re.I):
        kind, args = "run_shortcut", {"shortcut": match[1]}
    else:
        raise PlanError("Try ‘open Safari’, ‘note Idea: a better morning’, ‘timer for 25 minutes’, or a saved routine. Use local Ollama for more flexible phrasing.")
    return kind, [{"kind": kind, "args": args}]


class Planner:
    def __init__(self, config: dict, decider: Decider, name: str = "rules"):
        if name not in {"rules", "ollama"}:
            raise PlanError("JARVIS_PLANNER must be rules or ollama.")
        self.config, self.decider, self.name = config, decider, name

    def plan(self, text: str) -> tuple[list[dict], dict]:
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise PlanError("Write a request between 1 and 2,000 characters.")
        text = text.strip()
        # Saved routines are explicit, deterministic aliases, never rewritten by a generator.
        try:
            rule_intent, raw = parse_command(text, self.config)
        except PlanError:
            if self.name != "ollama" or re.match(r"(?:routine|start routine|run routine)\s", text, re.I):
                raise
            rule_intent, raw = "unknown", []
        decision = self.decider.decide(text, rule_intent)
        if rule_intent == "routine":
            if decision["intent"] not in {"routine", "compound"}:
                raise PlanError("The decision did not match this routine. Please rephrase.")
        elif self.name == "ollama" and not raw:
            raw = ollama_plan(text, self.config, decision["intent"])
            if self.decider.name != "rules" and decision["intent"] != "compound":
                if any(a.get("kind") != decision["intent"] for a in raw if isinstance(a, dict)):
                    raise PlanError("The planner and decision model disagreed. Please make the request more specific.")
        elif self.decider.name != "rules" and decision["intent"] != rule_intent:
            raise PlanError("The decision model and command parser disagreed. Please rephrase.")
        decision["explanation"] = (
            "Saved routine expanded into individual steps." if rule_intent == "routine" else
            "Explicit command parsed locally." if raw and rule_intent != "unknown" else
            "Local Ollama proposed these steps; check each target and detail."
        )
        if not raw:
            raise ProviderError("I need a clearer request before I can make a plan.")
        return raw, decision
