"""Render base .tex with a tagline from the fixed list, compile twice, verify against the ledger."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import yaml

from worker.doctor import pdflatex_path
from worker.tailor import verify_truth as vt


def taglines(data_dir: Path) -> dict[str, list[str]]:
    return yaml.safe_load((data_dir / "resumes" / ".." / "taglines.yaml").read_text())


def render_tex(key: str, tagline: str, data_dir: Path) -> str:
    allowed = taglines(data_dir).get(key, [])
    if tagline not in allowed:
        raise ValueError(f"tagline not in the fixed list for {key}")
    src = (data_dir / "resumes" / f"{key}.tex").read_text()
    # header is \resumeheader{name}{tagline}{contact}: replace the second brace group after \resumeheader
    m = re.search(r"(\\resumeheader\s*\{[^}]*\}\s*\{)(.*?)(\}\s*\{\\contactline\})", src, re.S)
    if not m:
        raise ValueError("resume header not found")
    suffix = ""
    if "$|$" in m.group(2):  # keep a trailing ' | City' if the base has one
        suffix = " \\ $|$ \\ " + m.group(2).split("$|$")[-1].strip().lstrip("\\").strip()
    return src[: m.start(2)] + tagline + suffix + src[m.end(2):]


def build(key: str, tagline: str, out_dir: Path, data_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    tex = render_tex(key, tagline, data_dir)
    (out_dir / "preamble.tex").write_text((data_dir / "resumes" / "preamble.tex").read_text())
    (out_dir / f"{key}.tex").write_text(tex)
    exe = pdflatex_path()
    if not exe:
        raise RuntimeError("pdflatex not found")
    for _ in range(2):
        subprocess.run([exe, "-interaction=nonstopmode", "-halt-on-error", f"{key}.tex"], cwd=out_dir,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=True)
    return out_dir / f"{key}.pdf"


def page_count(pdf: Path) -> int:
    log = pdf.with_suffix(".log").read_text(errors="ignore")
    m = re.search(r"Output written on .*?\((\d+) page", log)
    return int(m.group(1)) if m else -1


def pdf_text(pdf: Path) -> str:
    if shutil.which("pdftotext"):
        return subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, timeout=60).stdout
    return pdf.with_suffix(".tex").read_text()


def truth_check(pdf: Path, ledger_path: Path) -> list[str]:
    """Return FAIL messages from verify_truth (empty list == clean)."""
    text = vt.strip_latex_layout(pdf_text(pdf))
    ledger = vt.Ledger(yaml.safe_load(ledger_path.read_text()))
    return [msg for sev, msg in vt.check(text, ledger, False) if sev == "FAIL"]


def tailor(p, cfg, key: str | None = None) -> tuple[str, str]:
    """Pipeline stage: -> (resume_key, pdf_path). Falls back to the base tagline if the swap fails truth check."""
    from worker.tailor.pick_resume import pick

    key = key or pick(p)
    tl = taglines(cfg.data_dir)[key]
    out_dir = cfg.proof_dir / "resumes" / p.company.replace("/", "-")
    pdf = build(key, tl[0], out_dir, cfg.data_dir)
    if truth_check(pdf, cfg.data_dir / "ledger.yaml") or page_count(pdf) != 1:
        raise RuntimeError(f"resume {key} failed truth/page check")
    # the variant key names the company it was first tailored for; a recruiter must never see that in the filename
    from worker.apply.cover import slug
    role_dir = out_dir / slug(p.title)
    role_dir.mkdir(parents=True, exist_ok=True)
    named = role_dir / "Yogvid_Wankhede_Resume.pdf"
    shutil.copy(pdf, named)
    return key, str(named)
