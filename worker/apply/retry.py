"""Retry an unsubmitted form once its missing answers are saved; never retry uncertain submits."""
from hashlib import sha256
from pathlib import Path
import re
from urllib.parse import urlsplit

from worker.apply.base import LabelledFormFiller


def unanswered_questions(text):
    questions = []
    for part in text.split(';'):
        part = part.strip()
        if not part:
            continue
        # Semicolons in a single legal definition separate enumerated clauses, not form questions.
        if questions and re.match(r'^(or\s+)?\([ivx]+\)\s', part, re.I):
            questions[-1] += '; ' + part
        else:
            questions.append(part)
    return questions


def answer_revision(cfg) -> str:
    return sha256((cfg.data_dir / 'profile.yaml').read_bytes() +
                  (cfg.data_dir / 'answers.yaml').read_bytes() +
                  Path(__file__).with_name('base.py').read_bytes() +
                  Path(__file__).with_name('ashby.py').read_bytes() +
                  Path(__file__).with_name('ashby_state.py').read_bytes() +
                  Path(__file__).with_name('preferences.py').read_bytes() +
                  Path(__file__).with_name('projects.py').read_bytes() +
                  Path(__file__).with_name('generated.py').read_bytes() +
                  Path(__file__).with_name('answers.py').read_bytes() +
                  Path(__file__).with_name('lever.py').read_bytes() +
                  Path(__file__).read_bytes() +
                  Path(__file__).with_name('cover.py').read_bytes() +
                  (cfg.data_dir / 'cover_letter.tex').read_bytes() +
                  (cfg.data_dir / 'ledger.yaml').read_bytes()).hexdigest()[:16]


def resolved(job, profile, bank, revision: str) -> bool:
    if job.status != 'Needs you' or not job.questions_for_me or f'answers={revision}' in (job.notes or ''):
        return False
    questions = unanswered_questions(job.questions_for_me)
    # These outcomes might already have submitted, or need a platform action rather than an answer.
    if any(word in job.questions_for_me.lower() for word in
           ('captcha', 'human verification', 'employer rejected', 'apply by hand', 'submit did not confirm', 'submission interrupted',
            'verify the outcome', 'tracking lost')):
        return False
    if '(error:' in job.questions_for_me.lower():
        # GreenhouseFiller returns needs_you for field errors before Submit.
        # Ashby also uses it after validation, so do not generalize this retry.
        if urlsplit(job.apply_url or '').hostname not in {
                'job-boards.greenhouse.io', 'boards.greenhouse.io',
                'job-boards.eu.greenhouse.io', 'boards.eu.greenhouse.io'} or not re.search(r'\banswers=[^;]+;', job.notes or ''):
            return False
        questions = [re.sub(r'\s*\(error: TimeoutError\)$', '', q, flags=re.I) for q in questions]
        if any('(error:' in q.lower() for q in questions):
            return False
    from worker.models import Posting
    posting = Posting(company=job.company, title=job.role, tier=job.tier or 'startup',
                      url=job.source_url or job.apply_url or '', apply_url=job.apply_url or '', location=job.location or '')
    filler = LabelledFormFiller(None, profile, bank, Path('.'), Path('.'), posting=posting)
    from worker.apply.generated import eligible
    derive = (profile.get('preferences', {}).get('routine_answer_policy') == 'derive_from_saved_profile_and_verified_projects'
              and bool(profile.get('_verified_projects') or profile.get('_verified_experience')))
    from worker.apply.preferences import needs_interview_policy_context
    return bool(questions) and all(filler.profile_value(q) is not None or filler.answer_for(q) is not None or
                                   filler.AVAILABILITY.search(q) or q.lower() == 'cover letter (required)' or
                                   (needs_interview_policy_context(q) and isinstance(profile.get('preferences', {}).get('willing_to_complete_assessments'), bool)) or
                                   (derive and eligible(q)) for q in questions)
