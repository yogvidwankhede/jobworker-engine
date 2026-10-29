"""The leashed model call.

The model does not decide fit. It extracts the role's level, required experience, qualifications, and duties,
checking each against the JD. Code scores qualifications using the verified ledger; when no technical
qualifications are stated, it can instead score the posting's verified technical duties.

`score(p)` returns (score, reason) or None when the model output is unusable.
"""
from __future__ import annotations

import re

from worker.llm.ollama import generate_json as _generate_json
from worker.models import Posting
from worker.scoring import skills

generate_json = _generate_json  # module attribute so tests can monkeypatch

LEVELS = ["intern", "new_grad", "entry", "mid", "senior", "staff", "unclear"]
SCHEMA = {
    "type": "object",
    "properties": {
        "level": {"type": "string", "enum": LEVELS},
        "level_quote": {"type": "string"},
        "years_required": {"type": ["integer", "null"]},
        "years_quote": {"type": "string"},
        "must_have": {"type": "array", "items": {"type": "string"}},
        "responsibilities": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["level", "level_quote", "years_required", "years_quote", "must_have", "responsibilities"],
}

REQ_HEAD = re.compile(r"(requirements?|qualifications?|what you('ll| will)? (need|bring)|what we('re| are)? looking for|who you are|about you|"
                      r"you have|you should have|you might be a (good )?fit|you may be a (good )?fit|minimum|must[- ]have|skills? (and|&) experience|"
                      r"your background|ideal candidate|experience:|what you.ll need to (have|succeed)|we('re| are) looking for|you are|you bring|"
                      r"what it takes|what we need|the ideal|our ideal|basic qualifications|required)", re.I)
SENIOR_QUOTE = re.compile(r"\b(senior|staff|principal|lead|extensive|deep|expert|architect|mentor)\b", re.I)
YEARS_QUOTE = re.compile(r"(\d{1,2})\s*\+?\s*(?:(?:[-–—]|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)", re.I)


def requirements_excerpt(jd: str, limit: int = 7000) -> str:
    """Whole JD when it fits; otherwise the opening plus the text from the first heading-like requirements line."""
    if len(jd) <= limit:
        return jd
    pos = 0
    for m in re.finditer(r"^[ \t\-*•]*([^\n]{2,70})$", jd, re.M):
        line = m.group(1).strip()
        if REQ_HEAD.search(line) and len(line) <= 70 and not line.endswith(".") and m.start() > 300:
            pos = m.start()
            break
    if not pos:
        return jd[:limit]
    head = jd[:900]
    start = max(900, pos - 200)
    return head + "\n...\n" + jd[start:start + (limit - 900)]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9+#]+", " ", s.lower()).strip()


STOP = {"and", "the", "with", "for", "years", "experience", "strong", "ability", "proficiency", "knowledge", "familiarity",
        "of", "in", "or", "to", "a", "an", "as", "on", "at", "our", "your", "you", "we", "is", "are", "be", "have", "has"}


def _tokens(s: str) -> list[str]:
    return [w for w in _norm(s).split() if w not in STOP]


def _in_jd(item: str, jd_tokens: list[str]) -> bool:
    """An extracted requirement counts only if a run of 3 consecutive content words from it appears verbatim
    in the JD (2 words when the item is that short). Paraphrase and profile echo both fail this."""
    it = _tokens(item)
    if not it:
        return False
    n = min(3, len(it))
    grams = {tuple(it[i:i + n]) for i in range(len(it) - n + 1)}
    jd_grams = {tuple(jd_tokens[i:i + n]) for i in range(len(jd_tokens) - n + 1)}
    return bool(grams & jd_grams)


def extract(p: Posting) -> dict | None:
    prompt = (
        f"Job posting: {p.title} at {p.company}\nLocation: {p.location}\n\n{requirements_excerpt(p.jd_text)}\n\n"
        "You are extracting facts from the posting above. Return JSON with exactly these keys:\n"
        '  "level": one of intern | new_grad | entry | mid | senior | staff | unclear — the level the role is pitched at '
        "(use the title and the years/scope it asks for; 'senior' if it asks for 5+ years or senior ownership).\n"
        '  "level_quote": the phrase from the posting, COPIED VERBATIM, that shows the level ("" if nothing states it).\n'
        '  "years_required": integer minimum years of professional experience the posting REQUIRES, or null if it states none.\n'
        '  "years_quote": the phrase from the posting, COPIED VERBATIM, that states the years ("" if none).\n'
        '  "must_have": the REQUIRED qualifications (skills, tools, degrees, domains, experience), each COPIED VERBATIM '
        "from the posting's requirements/qualifications bullets — do not paraphrase, do not summarize, do not add anything "
        "that is not in the posting. Exclude nice-to-haves, preferred and bonus items. Up to 10 items; [] if the posting lists none.\n"
        '  "responsibilities": up to 5 technical duties, each COPIED VERBATIM from the posting. '
        "Use [] when none are stated. Keep responsibilities separate from required qualifications.\n"
        "No other keys."
    )
    out = None
    for _ in range(2):  # one retry: Ollama serialises calls, so a slow neighbour can push the first attempt past its timeout
        try:
            out = generate_json(prompt, SCHEMA)
            break
        except Exception:  # noqa: BLE001
            continue
    if not isinstance(out, dict):
        return None
    level, years, must = out.get("level"), out.get("years_required"), out.get("must_have")
    if level not in LEVELS:
        return None
    if not isinstance(must, list):
        return None
    jd_tokens = _tokens(p.jd_text)
    jd_norm = _norm(p.jd_text)
    # level: a senior/staff call stands only when its quote is in the JD and actually says something senior
    lq = out.get("level_quote") if isinstance(out.get("level_quote"), str) else ""
    senior_evidence = SENIOR_QUOTE.search(lq) or any(int(m.group(1)) >= 5 for m in YEARS_QUOTE.finditer(lq))
    if level in ("senior", "staff") and not (lq.strip() and _norm(lq) in jd_norm and senior_evidence):
        level = "unclear"
    # years: only when the quote is in the JD and contains the number
    yq = out.get("years_quote") if isinstance(out.get("years_quote"), str) else ""
    if years is not None and (not isinstance(years, int) or isinstance(years, bool) or not 0 <= years <= 30):
        years = None
    if years is not None:
        m = YEARS_QUOTE.search(yq or "")
        if not (m and _norm(yq) in jd_norm and int(m.group(1)) == years):
            years = None
    verified = [m.strip() for m in must if isinstance(m, str) and m.strip() and _in_jd(m, jd_tokens)][:12]
    responsibilities = out.get('responsibilities', [])
    if not isinstance(responsibilities, list):
        return None
    duties = [r.strip() for r in responsibilities if isinstance(r, str) and _norm(r) and _norm(r) in jd_norm][:5]
    return {"level": level, "years_required": years, "must_have": verified,
            'requirements_verified': len(verified) == len(must), 'responsibilities': duties}


SOFT = {"Communication", "Problem solving", "Degree"}
SOFT_TEXT = re.compile(r"\b(team|collaborat|communicat|passion|motivat|curio|fast-paced|independen|supervision|attitude|"
                       r"willing|eager|interest in|ability to (learn|work)|self-starter|ownership|humble|humility|empath|mission|culture|"
                       r"ship(ping)? quickly|move fast|bias for action|high agency|pragmatic|thoughtful|creativ|degree|bachelor|master|"
                       r"approach|impact|taste|mindset|values|excit|enthusias|opinion|judg|grit|hustle|resilien|adaptab|flexib|"
                       r"organi[sz]ed|detail|attention|proactive|initiative|accountab|kind|caring|integrity|ambigu|comfort|adoption|"
                       r"drive|driving|tinker|builder|hands-on attitude|energy|hungry|scrappy|ego|feedback|growth mindset|learner|personab|relationship|"
                       r"interpersonal|stakeholder management|customer[- ]facing|presence|charisma|persua|care(?:s)? (?:deeply )?about|"
                       r"bias for using the right tools|tradeoffs between (speed|quality) and (accuracy|speed))", re.I)
HARD_MISS = ("c++", "rust", "golang", "go", "java", "c#", "phd", "ph.d", "cuda", "spark", "pyspark", "clearance", "post-training", "rlhf",
             "reinforcement learning", "distributed training", "publications", "kubernetes", "terraform", "ansible", "chef", "iam",
             "authentication", "authorization", "identity", "airflow", "flyte", "ray", "bare metal", "on-call", "speech", "stt", "asr", "audio", "gpu", "profiling")


def decide(ext: dict, max_years: int) -> tuple[int, str]:
    """Turn a verified extraction into (score, reason). Deterministic; the model has no vote on fit."""
    if ext["level"] in ("senior", "staff"):
        return 0, f"model reads role as {ext['level']}-level"
    if ext["years_required"] is not None and ext["years_required"] > max_years:
        return 0, f"requires {ext['years_required']} years (limit {max_years})"
    def coverage(items):
        has, missing, soft_ok = [], [], 0
        for r in items:
            ok, why = skills.classify(r)
            if ok and why.split(" (")[0] in SOFT:
                soft_ok += 1
                continue
            if not ok and why == "no ledger evidence" and SOFT_TEXT.search(r):
                continue
            (has if ok else missing).append(r.strip())
        return has, missing, soft_ok

    has, missing, soft_ok = coverage(ext['must_have'])
    basis = 'technical must-haves'
    if not has and not missing and ext.get('requirements_verified') and ext.get('responsibilities'):
        # Some legitimate roles describe the work without a qualifications
        # list. Match their verified duties instead of imposing a score of 60.
        # Duties never override an explicit missing requirement.
        has, missing, soft_ok = coverage(ext['responsibilities'])
        basis = 'documented technical responsibilities (no stated technical must-haves)'
    n = len(has) + len(missing)
    if n == 0:
        return 60, f"no verifiable technical requirements ({soft_ok} soft items only)"
    confidence = {1: 0.7, 2: 0.85}.get(n, 1.0)  # one or two technical must-haves is thin evidence
    sc = round(100 * (len(has) / n) * confidence)
    if ext["level"] == "intern":
        sc -= 20
    hard_missing = [m for m in missing if any(re.search(r"(?<![a-z0-9+#])" + re.escape(x) + r"(?![a-z0-9+#])", m.lower()) for x in HARD_MISS)]
    sc -= 15 * len(hard_missing)
    sc = max(0, min(100, sc))
    reason = f"{len(has)}/{n} {basis} covered; missing: {', '.join(missing) if missing else 'none'}"
    return sc, reason


def score(p: Posting, max_years: int = 3) -> tuple[int, str] | None:
    ext = extract(p)
    if ext is None:
        return None
    return decide(ext, max_years)
