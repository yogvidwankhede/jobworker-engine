#!/usr/bin/env python3
"""
verify_truth.py — hard gate against fabrication.

Compares a generated resume against the truth ledger and fails on any number,
employer, date, URL, or forbidden claim that has no ledger counterpart.

    python scripts/verify_truth.py --resume out/resume.txt --ledger assets/truth-base.yaml

Exit codes:
    0  clean
    1  violations found (BLOCKER — fix the resume, not the ledger)
    2  usage / IO error

Scope note: this check is lexical. It reliably catches invented numbers,
employers, dates and links. It cannot catch a semantically false rearrangement of
true tokens (e.g. attributing a real metric to the wrong real mechanism). Stage 10's
credibility scan is the backstop for that. Treat a clean run as a floor, not proof.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write("PyYAML required:  pip install pyyaml --break-system-packages\n")
    raise SystemExit(2)


# --------------------------------------------------------------------------- #
# normalization
# --------------------------------------------------------------------------- #

def norm(text: str) -> str:
    """Fold to a comparable form: NFKD, lowercase, unify dashes/arrows/quotes."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    for a, b in (
        ("\u2013", "-"), ("\u2014", "-"), ("\u2212", "-"),
        ("\u2192", "->"), ("\u2019", "'"), ("\u201c", '"'), ("\u201d", '"'),
        ("\u00b1", "+/-"), ("\u2264", "<="), ("\u2265", ">="), ("~", ""),
    ):
        text = text.replace(a, b)
    text = re.sub(r"\\[a-zA-Z]+\s*", " ", text)     # strip LaTeX macros
    text = re.sub(r"[{}$\\]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


LATEX_LAYOUT_CMDS = (
    "vspace", "hspace", "fontsize", "linespread", "setlength", "smallskip",
    "medskip", "bigskip", "baselineskip", "parskip", "parindent", "arraystretch",
    "vskip", "hskip", "rule", "raisebox", "scalebox",
)


def strip_latex_layout(text: str) -> str:
    """Remove comments, the preamble, and layout-only numeric arguments.

    Without this, a .tex resume fails on \\vspace{-0.7em} and \\fontsize{9.5}{10.0}
    — real numbers that are typography, not claims.
    """
    if "\\begin{document}" not in text and "\\documentclass" not in text:
        return text
    text = re.sub(r"(?<!\\)%.*", "", text)                    # comments
    if "\\begin{document}" in text:
        text = text.split("\\begin{document}", 1)[1]
    text = text.split("\\end{document}", 1)[0]
    cmds = "|".join(LATEX_LAYOUT_CMDS)
    # \cmd{..}{..} / \cmd*{..} / \cmd 1.0
    text = re.sub(rf"\\(?:{cmds})\*?(?:\s*\{{[^{{}}]*\}})*", " ", text)
    text = re.sub(rf"\\(?:{cmds})\*?\s*[-\d.]+\s*(?:em|ex|pt|in|cm|mm)?", " ", text)
    text = re.sub(r"\[[^\[\]]*?(?:itemsep|topsep|parsep|leftmargin|label)[^\[\]]*?\]", " ", text)
    # row-break spacing:  \\[0.35em]
    text = re.sub(r"\\\\\s*\[[-\d.]+\s*(?:em|ex|pt|in|cm|mm)?\]", " ", text)
    return text


def flatten(node, out: list[str]) -> list[str]:
    """Collect every scalar in the ledger into a flat list of strings."""
    if isinstance(node, dict):
        for k, v in node.items():
            out.append(str(k))
            flatten(v, out)
    elif isinstance(node, list):
        for v in node:
            flatten(v, out)
    elif node is not None:
        out.append(str(node))
    return out


# --------------------------------------------------------------------------- #
# extraction
# --------------------------------------------------------------------------- #

NUM_RE = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*(%|k\+|k|m\+|m|\+)?", re.I)
URL_RE = re.compile(r"https?://[^\s)\]}>,;\"']+")
MONTH = (
    "january|february|march|april|may|june|july|august|september|october|"
    "november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
DATE_RE = re.compile(rf"({MONTH})\s+(\d{{4}})", re.I)

MONTH_NUM = {
    "january": "01", "jan": "01", "february": "02", "feb": "02",
    "march": "03", "mar": "03", "april": "04", "apr": "04", "may": "05",
    "june": "06", "jun": "06", "july": "07", "jul": "07", "august": "08",
    "aug": "08", "september": "09", "sep": "09", "sept": "09",
    "october": "10", "oct": "10", "november": "11", "nov": "11",
    "december": "12", "dec": "12",
}

# Numbers that are structural rather than claims.
BENIGN_NUMBERS = {"1", "2", "3", "4", "5", "0"}


def numbers_in(text: str) -> set[str]:
    found = set()
    for m in NUM_RE.finditer(text):
        raw = m.group(1).replace(",", "")
        if raw in BENIGN_NUMBERS:
            continue
        found.add(raw)
    return found


def dates_in(text: str) -> set[str]:
    return {
        f"{m.group(2)}-{MONTH_NUM[m.group(1).lower()]}"
        for m in DATE_RE.finditer(text)
    }


def urls_in(text: str) -> set[str]:
    return {u.rstrip(".,);") for u in URL_RE.findall(text)}


# --------------------------------------------------------------------------- #
# ledger view
# --------------------------------------------------------------------------- #

class Ledger:
    def __init__(self, data: dict):
        self.data = data
        scalars = flatten(data, [])
        self.blob = norm(" ".join(scalars))
        self.numbers = numbers_in(self.blob)
        # ledger dates are ISO (2026-06); resume dates normalize to YYYY-MM
        self.dates = set()
        for s in scalars:
            for m in re.finditer(r"\b(\d{4})-(\d{2})\b", s):
                self.dates.add(f"{m.group(1)}-{m.group(2)}")
            for m in DATE_RE.finditer(s):
                self.dates.add(f"{m.group(2)}-{MONTH_NUM[m.group(1).lower()]}")
        self.urls = {u.rstrip(".,);") for s in scalars for u in URL_RE.findall(s)}
        self.employers = {
            norm(str(e.get("employer", ""))) for e in data.get("experience", [])
        } | {
            norm(str(e.get("employer_short", "")))
            for e in data.get("experience", [])
            if e.get("employer_short")
        }
        self.employers.discard("")
        self.forbidden = [
            (str(f.get("claim", "")), str(f.get("reason", "")))
            for f in data.get("forbidden_claims", [])
        ]
        self.skill_terms = set()
        for group in (data.get("skills") or {}).values():
            if isinstance(group, dict):
                self.skill_terms |= {norm(k) for k in group}
            elif isinstance(group, list):
                self.skill_terms |= {norm(str(k)) for k in group}


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #

def check(resume: str, ledger: Ledger, strict_dates: bool) -> list[tuple[str, str]]:
    """Return a list of (severity, message)."""
    problems: list[tuple[str, str]] = []
    body = norm(resume)

    # 1. numbers
    for n in sorted(numbers_in(body), key=lambda s: (-len(s), s)):
        if n in ledger.numbers:
            continue
        # a number may legitimately appear as part of a larger ledger number
        if any(n in led for led in ledger.numbers):
            continue
        problems.append(("FAIL", f"number {n!r} appears in the resume but not in the ledger"))

    # 2. dates
    for d in sorted(dates_in(resume)):
        if d not in ledger.dates:
            sev = "FAIL" if strict_dates else "WARN"
            problems.append((sev, f"date {d} has no ledger counterpart"))

    # 3. urls
    for u in sorted(urls_in(resume)):
        if not any(u.rstrip("/") in led or led.rstrip("/") in u for led in ledger.urls):
            problems.append(("FAIL", f"URL {u} is not in the ledger"))

    # 4. forbidden claims — proximity heuristic.
    #    Bag-of-words over the whole document produces constant false positives
    #    (every word of a forbidden claim exists SOMEWHERE on a resume). Match
    #    within a sliding window instead, so only co-located terms count.
    STOP = {"with", "that", "this", "from", "into", "over", "work", "using",
            "used", "also", "your", "have", "held", "held.", "held", "held",
            "actually", "generic", "plural", "title", "held", "local"}
    units = [norm(u) for u in re.split(r"(?:\n|(?<=[.;])\s)", resume) if u.strip()]
    for claim, reason in ledger.forbidden:
        tokens = [t for t in re.findall(r"[a-z][a-z0-9-]{3,}", norm(claim))
                  if t not in STOP]
        # drop parenthetical editorial in the claim text
        if not tokens:
            continue
        need = max(3, int(len(tokens) * 0.8))
        for u in units:
            hits = sum(1 for t in set(tokens) if t in u)
            if hits >= min(need, len(set(tokens))):
                problems.append(
                    ("FAIL", f"line resembles a forbidden claim "
                             f"({hits}/{len(set(tokens))} terms co-located): "
                             f"{claim!r} — {reason}")
                )
                break

    # 5. employer sanity — only inspect lines that look like experience entries
    #    (a title/employer pair followed by a date range).
    entry_re = re.compile(
        rf"^(?!\s*[-*•])\s*\S.*?,\s*([A-Z][A-Za-z0-9&.,'\- ()]{{2,70}}?)\s*"
        rf"(?:\\hfill|\s{{2,}})\s*(?:{MONTH})\s+\d{{4}}", re.I | re.M)
    degree_re = re.compile(r"^\s*\{?\\?b?f?\}?\s*(master|bachelor|doctor|ph\.?d|b\.?s|m\.?s|b\.?tech|m\.?tech|associate)",
                           re.I)
    for m in entry_re.finditer(resume):
        if degree_re.match(m.group(0)):      # education lines, not employers
            continue
        cand = norm(m.group(1)).strip(" ,")
        if len(cand) < 4:
            continue
        if not any(cand in e or e in cand for e in ledger.employers):
            problems.append(("WARN", f"possible unknown employer: {m.group(1).strip()!r}"))

    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resume", required=True, help="generated resume (.txt/.md/.tex)")
    ap.add_argument("--ledger", required=True, help="truth-base.yaml")
    ap.add_argument("--strict-dates", action="store_true",
                    help="treat unmatched dates as failures (default: warn)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    try:
        resume = strip_latex_layout(Path(args.resume).read_text(encoding="utf-8"))
        ledger = Ledger(yaml.safe_load(Path(args.ledger).read_text(encoding="utf-8")))
    except OSError as e:
        sys.stderr.write(f"IO error: {e}\n")
        return 2

    problems = check(resume, ledger, args.strict_dates)
    fails = [p for p in problems if p[0] == "FAIL"]
    warns = [p for p in problems if p[0] == "WARN"]

    if not args.quiet:
        for sev, msg in fails:
            print(f"  [FAIL] {msg}")
        for sev, msg in warns:
            print(f"  [warn] {msg}")

    if fails:
        print(f"\nTRUTH CHECK FAILED — {len(fails)} violation(s), {len(warns)} warning(s).")
        print("Fix the RESUME, not the ledger — unless the candidate confirms the")
        print("ledger was genuinely missing a real fact.")
        return 1

    print(f"\nTruth check passed ({len(warns)} warning(s)).")
    print("Note: lexical check only. It cannot catch false causality — verify that")
    print("every metric is attributed to the mechanism that actually produced it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
