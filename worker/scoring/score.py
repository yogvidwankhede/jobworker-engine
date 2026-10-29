"""Rules first; model second; deterministic fallback always."""
from __future__ import annotations

from worker.config import Settings
from worker.models import Posting, Scored
from worker.scoring import llm, rules


def _from_model(p: Posting, got: tuple[int, str] | None, threshold: int = 70) -> Scored:
    if got is None:  # unverified → never selectable
        return Scored(posting=p, score=min(rules.heuristic_score(p), threshold - 1), reason="rules-only (model output unusable)")
    sc, reason = got
    return Scored(posting=p, score=sc, reason=reason, rejected=(sc == 0))


def score(p: Posting, s: Settings) -> Scored:
    rej = rules.check(p, s)
    if rej:
        return Scored(posting=p, score=0, reason=rej.reason, rejected=True)
    return _from_model(p, llm.score(p, s.max_years), s.fit_threshold)


def score_batch(postings, s: Settings, llm_score=None, llm_budget: int = 150, workers: int = 4, on_scored=None) -> list[Scored]:
    """Rules on everything; the model only on the top `llm_budget` by heuristic, `workers` calls in parallel."""
    from concurrent.futures import ThreadPoolExecutor

    llm_score = llm_score or (lambda p: llm.score(p, s.max_years))
    out: list[Scored] = []
    candidates: list[Posting] = []
    for p in postings:
        rej = rules.check(p, s)
        if rej:
            out.append(Scored(posting=p, score=0, reason=rej.reason, rejected=True))
        else:
            candidates.append(p)
    candidates.sort(key=lambda p: -rules.heuristic_score(p))
    top, rest = candidates[:llm_budget], candidates[llm_budget:]

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for scored in ex.map(lambda p: _from_model(p, llm_score(p), s.fit_threshold), top):
            out.append(scored)
            if on_scored:
                on_scored(scored)
    # Unchecked by the model → never selectable; capped just under the threshold so the digest still shows them.
    out.extend(Scored(posting=p, score=min(rules.heuristic_score(p), s.fit_threshold - 1), reason="rules-only (beyond model budget)") for p in rest)
    return out
