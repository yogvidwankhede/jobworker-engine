"""The answers bank: regex label -> typed answer. Unknown labels return None and are never guessed."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Answer:
    kind: str                 # text | select | yesno | checkbox
    answer: object            # str | bool | list[str]
    pattern: str
    only_if_required: bool = False   # e.g. GPA: given when a form insists, left blank when optional
    full: bool = False               # match against the whole label (question + follow-up), not just the question clause
    avoid: str = ""                  # options matching this regex are never chosen (race: never "East Asian"/"Southeast Asian")

    def choose(self, options: list[str]) -> str | None:
        """For select/checkbox answers: the first candidate present in the form's options (case-insensitive, substring ok)."""
        cands = self.answer if isinstance(self.answer, list) else [
            'Yes' if self.answer is True else 'No' if self.answer is False else str(self.answer)]
        if self.avoid:
            options = [o for o in options if not re.search(self.avoid, o, re.I)]
        norm = {o.strip().lower(): o for o in options}
        for c in cands:
            c_l = c.strip().lower()
            if c_l in norm:
                return norm[c_l]
        for c in cands:
            c_l = c.strip().lower()
            if len(c_l) < 4:          # short candidates (Yes/No/US) only match exactly or as a leading word
                for o_l, o in norm.items():
                    if re.match(rf"^{re.escape(c_l)}\b", o_l):
                        return o
                continue
            for o_l, o in norm.items():
                if o_l.startswith(c_l) or re.search(rf"\b{re.escape(c_l)}", o_l):
                    return o
        return None


# Labels that change the meaning of an otherwise-known question. The worker parks these rather than
# risk a false attestation (e.g. "authorized to work WITHOUT sponsorship", "certify I am a U.S. citizen").
GUARD = re.compile(r"without\s+((visa|company|employer)\s+)?sponsor|not\s+require\s+sponsor|\bcitizen|permanent\s+resident|green\s*card|clearance|"
                   r"\bwill\s+not\b|\bnever\b|do\s+not\s+require", re.I)
GUARD_OK = re.compile(r"clearance|person|export", re.I)   # clearance / U.S.-person questions have their own explicit rule (answer No)
# Boards append disclaimers ("(This information will not be used in hiring decisions)", "Note: we never share…")
# that trip GUARD although the question itself is the standard one. Only the question clause is guarded.
DISCLAIMER = re.compile(r"\b(this information|will not be used|not be used|does not (affect|impact)|no impact|for (statistical|reporting|compliance) purposes|"
                        r"we encourage|note:|optional)\b", re.I)


def question_clause(text: str) -> str:
    """The question itself: parenthetical/bracketed disclaimers removed, anything after the first '?' dropped."""
    def _strip(m):
        return " " if DISCLAIMER.search(m.group(0)) else m.group(0)
    t = re.sub(r"\([^()]*\)|\[[^\[\]]*\]", _strip, text)
    q = t.find("?")
    if q > 0:
        t = t[:q + 1]
    return re.sub(r"\s+", " ", t).strip()


class AnswerBank:
    def __init__(self, rules: list[tuple[re.Pattern, Answer]]):
        self.rules = rules

    @classmethod
    def load(cls, path: Path) -> "AnswerBank":
        data = yaml.safe_load(path.read_text())["answers"]
        rules = []
        for a in data:
            for pat in a["match"]:
                rules.append((re.compile(pat, re.I), Answer(a["kind"], a["answer"], pat, bool(a.get("only_if_required", False)), bool(a.get("full", False)), str(a.get("avoid", "")))))
        return cls(rules)

    def lookup(self, label: str) -> Answer | None:
        text = re.sub(r"\s+", " ", label or "").strip()
        clause = question_clause(text)
        for rx, ans in self.rules:                          # rules that need the follow-up text ("If yes, please share…")
            if ans.full and rx.search(text):
                return ans
        for candidate in dict.fromkeys([clause, text]):   # the question first, then the full label with its disclaimers
            for rx, ans in self.rules:
                if ans.full or not rx.search(candidate):
                    continue
                if GUARD.search(clause) and not (GUARD_OK.search(clause) and re.search(r"clearance|polygraph|person|export", rx.pattern)):
                    return None
                return ans
        return None
