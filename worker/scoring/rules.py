"""Deterministic gate. Anything rejected here never reaches the model.

Every rule answers one question: "would Yogvid be a real match for this posting?"
A posting must clear all of them before the model is asked anything.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from worker.config import Settings
from worker.models import Posting

IN_SCOPE = [
    r"\bai\b", r"\bml\b", r"machine learning", r"\bllm\b", r"gen(erative)? ?ai", r"data scien",
    r"forward deployed", r"applied ai", r"software engineer", r"full[- ]stack", r"backend engineer",
    r"platform engineer", r"evals?\b", r"agents?\b", r"nlp", r"research engineer", r"solutions engineer",
    r"data engineer", r"analytics engineer",
]
OUT_OF_SCOPE = [
    r"applied scientist", r"product manager", r"account executive", r"recruit", r"sales\b", r"designer",
    r"marketing", r"customer success", r"attorney", r"counsel", r"finance", r"accountant", r"hardware",
    r"\bqa\b", r"technical writer", r"support engineer",
    # domains with no ledger evidence
    r"\bios\b", r"android", r"\bmobile\b", r"embedded", r"firmware", r"compiler", r"\bkernel\b",
    r"\bsre\b", r"site reliability", r"security engineer", r"network engineer", r"front[- ]?end",
    r"\bgame\b", r"graphics", r"\bfpga\b", r"\basic\b", r"robotic", r"perception", r"autonom",
    r"\bdevops\b", r"\bgpu\b", r"\bcuda\b", r"silicon", r"\bchip\b", r"physical design", r"solutions architect",
    r"program manager", r"project manager", r"business analyst", r"help ?desk",
    # research-track (PhD in practice), public sector (citizenship in practice), go-to-market
    r"post-?doc", r"research scientist", r"researcher", r"applied research", r"ml scientist", r"ai scientist",
    r"public sector", r"federal", r"government", r"defense", r"\bgov\b", r"national security",
    r"strategist", r"partnership", r"\bgtm\b", r"go-to-market", r"evangelist", r"advocate", r"developer relations", r"devrel",
    r"consultant", r"pre-?sales", r"solutions? consultant", r"customer engineer", r"implementation",
    # infra / IT / ops
    r"\bit\s*/", r"\bit\b(?= |$)", r"information systems", r"internal infrastructure", r"infrastructure engineer", r"operations engineer",
    r"systems engineer", r"\bops\b", r"reliability", r"post-?training", r"pre-?training", r"inference", r"training infra", r"speech", r"audio", r"voice",
    r"\bbi\b", r"business intelligence", r"\binfra(structure)?\b", r"\bstorage\b", r"\bnetworking\b", r"\bcompute\b", r"\bidentity\b", r"\biam\b",
    r"apollo systems", r"\bkubernetes\b", r"\bk8s\b", r"\bcluster\b", r"distributed systems",
    r"distributed training", r"model training", r"training (infra|platform|systems?)", r"database engineering", r"query engine", r"storage engine",
    r"\bruntime\b", r"\bdatabase\b(?!.*\b(ai|llm|ml)\b)", r"\bedge\b", r"autonomous systems", r"\boperations\b", r"\bops\b",
    r"\bhardware\b", r"\bsensors?\b", r"\bdrone", r"\bvehicle",
    # model-lab research tracks
    r"reinforcement learning", r"\brl\b", r"\brlhf\b", r"alignment", r"interpretability", r"pretraining", r"pre-training", r"\bscaling\b",
    r"research engineer(?!.*\b(evals?|evaluation|agents?|applied|product|tooling|platform)\b)", r"research scientist", r"\bresearcher\b",
]
TOO_SENIOR = [
    r"\bsenior\b", r"\bsr\.?\b", r"\bstaff\b", r"\bprincipal\b", r"\bdistinguished\b", r"\bdirector\b", r"\bvp\b",
    r"vice president", r"\bhead of\b", r"\bmanager\b", r"\blead\b(?! engineer)", r"\bchief\b", r"\bfellow\b",
    r"\barchitect\b", r"\biii\b", r"\biv\b", r"\bexpert\b", r"\bexecutive\b",
    r"\b[rsea]vp\b", r"\bc[eot]o\b", r"\bgm\b", r"general manager", r"\bsupervisor\b",
]
INTERN = [r"\bintern(ship)?s?\b", r"\bco-?op\b", r"\bapprentice", r"\bsummer 20\d\d\b", r"\b(spring|fall|winter) 20\d\d\b"]
NOT_FULL_TIME = [r"\bcontract(or|ed)?\b", r"\bpart[- ]time\b", r"\btemporary\b", r"\btemp\b", r"fixed[- ]term", r"\bfreelance", r"\b1099\b",
                 r"\bhourly\b", r"\bseasonal\b", r"\bfellowship\b", r"\bvolunteer\b"]
NOT_FULL_TIME_JD = [r"this is a (\d+[- ]month |short[- ]term |temporary |part[- ]time |contract )+(position|role|engagement|contract)",
                    r"employment type:?\s*(contract|part[- ]time|temporary|intern)", r"\b(contract|part[- ]time|temporary)[- ]to[- ]hire\b"]
FUTURE_COHORT = [r"\b(2027|2028|2029)\b"]  # a class-of-2027 cohort starts a year from now
PHD_REQUIRED = [r"ph\.?d\.?\s*(is\s+)?required", r"(must|need to)\s+(have|hold)\s+a\s+ph\.?d", r"requires?\s+a\s+ph\.?d",
                r"ph\.?d\.?\s+in\s+[\w\s,/]+?\s+(is\s+)?required", r"doctora(l|te)\s+(degree\s+)?(is\s+)?required"]
NO_SPONSOR = [
    r"unable\s+to\s+sponsor", r"no\s+(visa\s+)?sponsorship", r"will\s+not\s+sponsor", r"not\s+able\s+to\s+(provide|offer)\s+(visa\s+)?sponsorship",
    r"cannot\s+sponsor", r"can\s*not\s+sponsor", r"do(es)?\s+not\s+(provide|offer)\s+(visa\s+)?sponsorship", r"without\s+sponsorship",
    r"must\s+be\s+(a\s+)?(u\.?s\.?|us)\s+citizen", r"(u\.?s\.?|us)\s+citizens?\s+only", r"citizenship\s+(is\s+)?required",
    r"security\s+clearance", r"(ts|top\s+secret)/?sci", r"green\s*card\s+(holders?\s+)?(only|required)",
    r"(do|does|will)\s+not\s+sponsor", r"(won'?t|wont)\s+sponsor", r"not\s+able\s+to\s+sponsor", r"unable\s+to\s+(provide|offer)\s+(visa\s+)?sponsorship",
    r"sponsorship\s+(is\s+)?(not\s+available|unavailable)", r"not\s+eligible\s+for\s+(visa\s+)?sponsorship", r"(cannot|can\s*not|unable\s+to)\s+(provide|offer)\s+(visa\s+)?sponsorship",
    r"no\s+visa\s+sponsorship", r"sponsorship\s+(is\s+)?not\s+(offered|provided|possible)",
    r"(u\.?s\.?|us)\s+person(s)?\s+(only|required)", r"\bitar\b",
]
US_HINTS = [r"\bremote\b", r"united states", r"\busa?\b", r"\bu\.s\.", r", [A-Z]{2}\b", r"\b(san francisco|new york|chicago|seattle|austin|boston|denver|los angeles|st\. louis|palo alto|mountain view|menlo park|sunnyvale|redmond|atlanta|dallas|houston|miami|washington|philadelphia|nashville|salt lake city|bay area|nyc|sf)\b"]
NON_US = [r"\b(uk|united kingdom|london|canada|toronto|vancouver|montreal|india|bangalore|bengaluru|hyderabad|pune|mumbai|chennai|berlin|germany|munich|paris|france|amsterdam|netherlands|dublin|ireland|singapore|sydney|melbourne|australia|tokyo|japan|israel|tel aviv|poland|warsaw|krakow|spain|madrid|barcelona|portugal|lisbon|brazil|sao paulo|mexico|zurich|switzerland|sweden|stockholm|emea|apac|latam|europe|asia|africa|korea|seoul|taiwan|china|shanghai|beijing|hong kong|dubai|uae|nigeria|argentina|colombia|chile|peru|philippines|vietnam|indonesia|malaysia|thailand|italy|milan|rome|belgium|brussels|austria|vienna|denmark|copenhagen|norway|oslo|finland|helsinki|czech|prague|hungary|budapest|romania|bucharest|ukraine|kyiv|turkey|istanbul|egypt|south africa|new zealand|auckland|middle east|mena|saudi|riyadh|qatar|doha|kuwait|bahrain|oman|jordan|lebanon|pakistan|bangladesh|sri lanka|nepal|latin america|south america|central america|costa rica|uruguay|ecuador|guatemala|panama|caribbean|oceania|nordics|benelux|dach|iberia|baltics|balkans|cee|anz|apj|americas(?! .*united states)|worldwide|global)\b"]
LANGUAGE_JD = [r"(fluen(t|cy)|proficien\w*|native|business[- ]level|professional)\s+(in\s+)?(both\s+)?(english\s+and\s+)?(korean|japanese|mandarin|cantonese|chinese|german|french|spanish|portuguese|arabic|hindi|italian|dutch|hebrew|russian|polish|turkish|vietnamese|thai|indonesian|swedish|norwegian|danish|finnish)\b",
               r"\b(korean|japanese|mandarin|cantonese|chinese|german|french|spanish|portuguese|arabic|hindi|italian|dutch|hebrew|russian|polish|turkish|vietnamese|thai|indonesian|swedish|norwegian|danish|finnish)\s+(language\s+)?(skills?\s+)?(is\s+|are\s+)?(required|a must|mandatory|essential)"]
LANGUAGE_TITLE = [r"\b\w+[- ]speaking\b", r"\bbilingual\b", r"\bfluent\b", r"\b(portuguese|spanish|german|french|japanese|korean|mandarin|chinese|hindi|arabic|italian|dutch|hebrew|russian|polish|turkish|vietnamese|thai|indonesian)\b"]

# Languages that a JD can require which the ledger does not support at a professional level.
FOREIGN_STACK = {
    "c++": r"\bc\+\+", "rust": r"\brust\b", "go": r"\bgolang\b|\bgo (programming|developer|engineer|lang)\b|\(go\)",
    "java": r"\bjava\b(?!script)", "c#": r"\bc#|\.net\b", "swift": r"\bswift\b", "kotlin": r"\bkotlin\b", "scala": r"\bscala\b",
    "ruby": r"\bruby\b", "php": r"\bphp\b", "elixir": r"\belixir\b", "erlang": r"\berlang\b", "haskell": r"\bhaskell\b",
}
HOME_STACK = r"\b(python|typescript|javascript|node(\.js)?|nestjs|sql)\b"

YEARS_RE = re.compile(
    r"(?:(?:minimum|min\.?|at least|over|more than)\s+(?:of\s+)?)?(\d{1,2})\s*(?:\+|\s*-\s*\d{1,2}|\s*to\s+\d{1,2}|–\s*\d{1,2})?\s*\+?\s*(?:years?|yrs?)\b"
    r"(?:\s+(?:of|in|with))?[^.\n;]{0,80}?\b(experience|background|track record|working|building|developing|shipping|hands-on|professional|industry|relevant)\b",
    re.I,
)
YEARS_ALT_RE = re.compile(r"\b(experience|background)\b[^.\n;]{0,40}?\b(?:of\s+)?(?:minimum|min\.?|at least)?\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b", re.I)
PREFERRED_HEAD = re.compile(r"(preferred|nice[- ]to[- ]have|bonus|plus(es)?\b|would be great|extra credit|good to have|not required)", re.I)
COMPANY_CONTEXT = re.compile(r"\b(we have|our (team|company|founders?|leadership)|founded|combined|collectively|has been|track record of the)\b", re.I)


# Title families in Yogvid's priority order. Lower rank = applied first.
FAMILIES = [
    (0, "AI Engineer", [r"\bai engineer", r"applied ai", r"\bgen(erative)? ?ai\b", r"forward deployed", r"\bai (product|platform|software|solutions|application|systems?|enablement) engineer", r"engineer,? ai\b", r"ai/ml engineer", r"\bai\b.*\bengineer"]),
    (1, "LLM Engineer", [r"\bllm", r"\bagents?\b", r"\bagentic\b", r"prompt engineer", r"\bevals?\b", r"\brag\b", r"conversational ai", r"language model"]),
    (2, "Machine Learning Engineer", [r"machine learning engineer", r"\bml engineer", r"\bmle\b", r"machine learning", r"\bml\b", r"deep learning", r"\bnlp\b", r"research engineer"]),
    (3, "Data Scientist", [r"data scien", r"\bstatistic", r"analytics engineer", r"data analyst"]),
    (4, "Software Engineer (AI/ML)", [r"software engineer", r"full[- ]?stack", r"backend", r"platform engineer", r"solutions engineer", r"data engineer", r"\bengineer\b"]),
]
AI_SIGNAL = [r"\bllm", r"large language model", r"machine learning", r"\bml\b", r"\bai\b", r"gen(erative)? ?ai", r"\bagents?\b", r"\bagentic\b", r"\brag\b", r"\bnlp\b", r"deep learning", r"foundation model", r"\bgpt\b", r"openai", r"anthropic", r"\bevals?\b"]
# For a plain software-engineer title the JD must be about AI/ML work, not merely mention "AI" in company boilerplate:
STRONG_AI = [r"\bllms?\b", r"large language model", r"machine learning", r"gen(erative)? ?ai", r"\bagentic\b", r"ai agents?", r"\brag\b", r"retrieval[- ]augmented",
             r"foundation model", r"deep learning", r"\bnlp\b", r"prompt(ing| engineering)", r"vector (database|store|search)", r"embeddings?\b", r"fine-?tun", r"\bml (models?|systems?|pipelines?|platform)"]


def _strong_ai_count(text: str) -> int:
    t = re.sub(r"\s+", " ", text.lower())
    return sum(1 for pat in STRONG_AI if re.search(pat, t))


def title_family(p: Posting) -> tuple[int, str]:
    """(rank, family name). Software-engineer titles count only when the title or JD carries an AI/ML signal."""
    title = p.title.lower()
    for rank, name, pats in FAMILIES:
        if _any(pats, title):
            if rank == 4 and not (_any(AI_SIGNAL, title) or _strong_ai_count(p.jd_text) >= 3):
                return 9, "Software Engineer (no AI/ML signal)"
            return rank, name
    return 9, "other"


@dataclass
class Rejection:
    reason: str


def _fold(text: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _any(patterns: list[str], text: str) -> str | None:
    t = re.sub(r"\s+", " ", _fold(text).lower())
    for pat in patterns:
        m = re.search(pat, t, re.I)
        if m:
            return m.group(0)
    return None


def _segments(jd: str) -> list[tuple[bool, str]]:
    """Split a JD into (is_preferred, text) segments at heading-like lines."""
    segs, preferred, buf = [], False, []
    for line in jd.splitlines():
        s = line.strip()
        heading = s and len(s) <= 60 and (s.endswith(":") or (s[0].isupper() and not s.endswith(".") and len(s.split()) <= 7))
        if heading:
            if buf:
                segs.append((preferred, "\n".join(buf)))
                buf = []
            preferred = bool(PREFERRED_HEAD.search(s))
        buf.append(s)
    if buf:
        segs.append((preferred, "\n".join(buf)))
    return segs


def years_required(jd: str) -> int | None:
    """Minimum years of experience the JD requires (lower bound of any range), ignoring preferred/bonus sections
    and sentences about the company itself. None when the JD states nothing."""
    found: list[int] = []
    for preferred, seg in _segments(jd):
        if preferred:
            continue
        for sentence in re.split(r"(?<=[.;\n])\s+|\n", seg):
            if COMPANY_CONTEXT.search(sentence):
                continue
            for m in YEARS_RE.finditer(sentence):
                found.append(int(m.group(1)))
            for m in YEARS_ALT_RE.finditer(sentence):
                found.append(int(m.group(2)))
    found = [n for n in found if 0 <= n <= 25]
    return max(found) if found else None


def foreign_stack(jd: str) -> str | None:
    """A language the JD centers on that the ledger cannot support, when none of the home stack appears at all."""
    t = jd.lower()
    if re.search(HOME_STACK, t):
        return None
    for name, pat in FOREIGN_STACK.items():
        if re.search(pat, t):
            return name
    return None


def check(p: Posting, s: Settings) -> Rejection | None:
    employment = re.sub(r'[^a-z]', '', p.employment_type.lower())
    if employment and employment != 'fulltime':
        if not (s.include_internships and employment in {'intern', 'internship'}):
            return Rejection(f'not full-time: board employment type {p.employment_type}')
    title = p.title.lower()
    if (m := _any(OUT_OF_SCOPE, title)):
        return Rejection(f"title out of scope: '{m}'")
    if (m := _any(TOO_SENIOR, title)):
        return Rejection(f"seniority above mid: '{m}'")
    if not s.include_internships and (m := _any(INTERN, title)):
        return Rejection(f"internship/co-op: '{m}'")
    if (m := _any(NOT_FULL_TIME, title)) or (m := _any(NOT_FULL_TIME_JD, p.jd_text)):
        return Rejection(f"not full-time: '{m}'")
    if (m := _any(FUTURE_COHORT, title)):
        return Rejection(f"future cohort: '{m}'")
    if (m := _any(LANGUAGE_TITLE, title)):
        return Rejection(f"language-specific role: '{m}'")
    if (m := _any(NON_US, title)) and not _any([r"\b(us|usa|united states)\b"], title):
        return Rejection(f"non-US role in title: '{m}'")
    if not _any(IN_SCOPE, title):
        return Rejection("title not in target list")
    fam_rank, fam = title_family(p)
    if fam_rank == 9:
        return Rejection(f"not an AI/ML role: {fam}")
    if (m := _any(NO_SPONSOR, p.jd_text)):
        return Rejection(f"no sponsorship / citizenship / clearance: '{m}'")
    if (m := _any(LANGUAGE_JD, p.jd_text)):
        return Rejection(f"language requirement: '{m}'")
    if (m := _any(PHD_REQUIRED, p.jd_text)):
        return Rejection(f"PhD required: '{m}'")
    yrs = years_required(p.jd_text)
    if yrs is not None and yrs > s.max_years:
        return Rejection(f"requires {yrs}+ years (limit {s.max_years})")
    if (m := foreign_stack(p.jd_text)):
        return Rejection(f"stack mismatch: JD centers on {m}, no Python/TypeScript/SQL")
    loc = p.location or ""
    strong_us = _any([r"\bremote\b", r"united states", r"\busa?\b", r"\bu\.s\."], loc)
    if loc and not strong_us and _any(NON_US, loc):
        return Rejection(f"location outside US: '{loc}'")
    if loc and strong_us and re.search(r"\bremote\b", loc, re.I) and _any(NON_US, loc):
        return Rejection(f"remote outside US: '{loc}'")
    if loc and not strong_us and not _any(US_HINTS, loc) and not re.search(r"\bremote\b", p.jd_text, re.I):
        return Rejection(f"location unclear/non-US: '{loc}'")
    return None


def heuristic_score(p: Posting) -> int:
    """Rules-only fallback: keyword overlap with the target stack, 40-85."""
    kws = ["llm", "rag", "langchain", "langgraph", "agent", "eval", "python", "typescript", "gcp", "docker",
           "vector", "embedding", "fine-tun", "pytorch", "prompt", "openai", "anthropic", "claude", "retrieval"]
    t = (p.title + " " + p.jd_text).lower()
    hits = sum(1 for k in kws if k in t)
    base = 40 + min(hits, 9) * 5
    if re.search(r"\b(ii|2|new grad|early career|entry|junior|associate)\b", p.title.lower()):
        base += 5
    return min(base, 85)
