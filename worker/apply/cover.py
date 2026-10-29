"""Required cover letters compiled from verified facts without consuming model quota."""
from __future__ import annotations

import re
from pathlib import Path


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def cover_letter_dir(cfg) -> Path:
    return cfg.proof_dir.parent / "cover_letters"


def cover_letter_path(cfg, posting) -> Path:
    return cover_letter_dir(cfg) / f"{slug(posting.company)}__{slug(posting.title)}.pdf"


def letter_text(posting, profile):
    from worker.apply.projects import select_project, describe

    project = select_project(posting, profile.get('_verified_projects', []))
    if not project:
        raise ValueError('No verified project evidence for the required cover letter')
    first = (f"For the {posting.title} role at {posting.company}, I'd bring experience building AI workflows. "
             "At EUNO, I build LLM workflows with LangGraph.js, NestJS, and TypeScript and deploy scheduled workers on GCP.")
    second = describe(project)
    third = ("I'm an early-career engineer. My AI engineering experience comes from internships, research, "
             "and independent projects, and I want to keep building on that foundation.")
    topics = [('retrieval', 'retrieval'), ('RAG', 'retrieval-augmented generation'), ('evaluation', 'evaluation'),
              ('agents', 'AI agents'), ('LangGraph', 'LangGraph'), ('LLM', 'LLM applications')]
    topic = next((phrase for token, phrase in topics if re.search(r'\b' + re.escape(token) + r'\b', posting.jd_text, re.I)), None)
    fourth = (f"The posting's focus on {topic} interests me. " if topic else f"I want to contribute to the work described for the {posting.title} role. ")
    fourth += "And I want to turn that work into production AI products with real user impact."
    return [p.replace('—', ', ').replace('–', ', ') for p in (first, second, third, fourth)]


def ensure_letter(cfg, posting, profile) -> Path:
    import subprocess
    from worker.doctor import pdflatex_path
    from worker.local_time import today
    from worker.tailor.build_pdf import page_count, truth_check

    existing = cover_letter_path(cfg, posting)
    if existing and existing.is_file():
        return existing
    paragraphs = letter_text(posting, profile)
    out = cfg.proof_dir / 'cover_letters' / f'{slug(posting.company)}__{slug(posting.title)}'
    out.mkdir(parents=True, exist_ok=True)
    pdf = out / 'Yogvid_Wankhede_Cover_Letter.pdf'
    if pdf.is_file() and pdf.with_suffix('.txt').is_file() and pdf.with_suffix('.txt').read_text() == '\n\n'.join(paragraphs):
        return pdf
    escapes = {'\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#',
               '_': r'\_', '{': r'\{', '}': r'\}', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
    escape = lambda value: ''.join(escapes.get(ch, ch) for ch in str(value))
    contact = profile['contact']
    name = contact['first_name'] + ' ' + contact['last_name']
    template = (cfg.data_dir / 'cover_letter.tex').read_text()
    replacements = {'<LETTERHEAD>': r'{\LARGE\bfseries ' + escape(name) + r'}\\[0.35em]' + '\n' +
                    escape(contact['phone_display'] + ' | ' + contact['email']),
                    '<MONTH DAY, YEAR>': today().strftime('%B %d, %Y'),
                    '<HIRING TEAM / HIRING MANAGER NAME IF KNOWN>': 'Hiring Team',
                    '<COMPANY NAME>': escape(posting.company),
                    '<HIRING MANAGER NAME, or "Hiring Team">': 'Hiring Team'}
    replacements.update({f'<PARAGRAPH {i}>': escape(p) for i, p in enumerate(paragraphs, 1)})
    for key, value in replacements.items():
        template = template.replace(key, value)
    tex = pdf.with_suffix('.tex')
    tex.write_text(template)
    exe = pdflatex_path()
    if not exe:
        raise RuntimeError('pdflatex not found for required cover letter')
    subprocess.run([exe, '-interaction=nonstopmode', '-halt-on-error', tex.name], cwd=out,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=True)
    if page_count(pdf) != 1 or truth_check(pdf, cfg.data_dir / 'ledger.yaml'):
        pdf.unlink(missing_ok=True)
        raise RuntimeError('Required cover letter failed truth/page verification')
    pdf.with_suffix('.txt').write_text('\n\n'.join(paragraphs))
    return pdf
