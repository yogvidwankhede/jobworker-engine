"""Reuse verified scores for unchanged jobs instead of spending model quota every hour."""
from hashlib import sha256
import json
from pathlib import Path

from worker.cycle import save_state
from worker.models import Scored
from worker.scoring.score import score_batch


def batch(postings, settings, cfg):
    path = cfg.proof_dir / "scoring-cache.json"
    cache = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(cache, dict):
        raise ValueError("Invalid scoring cache")
    # Ledger, criteria, or scoring implementation changes invalidate earlier conclusions.
    source = Path(__file__).parent
    version = b''.join((source / name).read_bytes() for name in ('llm.py', 'rules.py', 'skills.py', 'score.py'))
    version += (cfg.data_dir / 'ledger.yaml').read_bytes()
    version += json.dumps({'max_years': settings.max_years, 'include_internships': settings.include_internships,
                           'fit_threshold': settings.fit_threshold}, sort_keys=True).encode()
    version = sha256(version).hexdigest()

    def key(p):
        return sha256((version + p.model_dump_json()).encode()).hexdigest()

    out, fresh = [], []
    for p in postings:
        saved = cache.get(key(p))
        if saved is None:
            fresh.append(p)
        else:
            out.append(Scored(posting=p, **saved))
    def remember(sc, *, checkpoint=False):
        if not sc.reason.startswith('rules-only'):
            cache[key(sc.posting)] = {'score': sc.score, 'reason': sc.reason, 'rejected': sc.rejected}
            if checkpoint:
                save_state(path, cache)
    # Preserve model progress immediately, but write bulk rule rejections only once.
    # Rewriting the entire cache per rejection can consume the application window.
    scored = score_batch(fresh, settings, llm_budget=settings.llm_budget, workers=settings.llm_workers,
                         on_scored=lambda sc: remember(sc, checkpoint=True))
    for sc in scored:
        remember(sc)
    save_state(path, cache)
    return out + scored
