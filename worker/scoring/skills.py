"""What the candidate can truthfully claim, derived from ledger.yaml, with aliases for matching JD phrases.

A JD requirement counts as "has" only if it names something on this list. The model never decides this.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# alias -> canonical. Aliases are lowercase substrings matched on word boundaries.
ALIASES: dict[str, list[str]] = {
    "Python": ["python", "pandas", "numpy", "flask", "fastapi", "scikit", "sklearn"],
    "TypeScript": ["typescript", "nestjs", "nest.js"],
    "JavaScript": ["javascript", "node.js", "nodejs", "node js", "next.js", "nextjs", "es6", "react.js", "reactjs", "react native"],
    "R": [r"\br\b (programming|language)", "r/python", "python/r"],
    "LLM systems": ["llm", "large language model", "foundation model", "generative ai", "genai", "gen ai", "gpt", "openai", "anthropic", "claude", "gemini", "transformer", "ai tools", "ai coding", "ai-assisted", "ai assisted", "copilot", "cursor", "codex", "coding agent", "coding assistant", "ai-native", "ai native"],
    "RAG": ["rag", "retrieval", "retrieval-augmented", "vector", "embedding", "pinecone", "semantic search"],
    "SQL": ["sql", "postgres", "postgresql", "mysql", "relational database", "databases"],
    "Agents": ["agent", "agentic", "langgraph", "langchain", "agent orchestration", "llm orchestration", "orchestration framework", "tool use", "tool calling", "function calling", "mcp", "react or plan", "plan-and-execute"],
    "Prompt engineering": ["prompt"],
    "Evals": ["eval", "evaluation", "benchmark", "llm-as-judge", "quality measurement", "a/b test", "experiment"],
    "Fine-tuning": ["fine-tun", "finetun", "lora", "peft", "sentence-transformers", "hugging face", "huggingface"],
    "Machine learning": ["machine learning", r"\bml\b", "deep learning", "neural", "pytorch", "tensorflow", "keras", "model training", "data scien", "classification", "regression", "forecasting", "time series", "nlp", "natural language", "computer vision", "statistical model", "predictive model", "xgboost", "random forest"],
    "Statistics": ["statistic", "hypothesis", "probability", "experimentation", "confidence interval", "causal", "a/b"],
    "Data analysis": ["data analysis", "analytics", "dashboard", "tableau", "power bi", r"\bexcel\b", "reporting", "data visualization", "visualization", "data-driven"],
    "Software engineering": ["software engineer", "software engineering", "engineering fundamentals", "data structures", "algorithm", "programming", "coding", "write code", "clean code", "production code", "shipped", "prototyp", "full-stack", "full stack", "fullstack", "web application", "computer science fundamentals", "cs fundamentals"],
    "APIs / backend": ["api", "rest", "backend", "back-end", "microservice", "web service", "server-side", "distributed system", "scalable", "production system", "production-grade"],
    "Cloud (GCP/AWS)": ["gcp", "google cloud", "aws", "amazon web services", "cloud", "cloud run", "ec2", "s3", "serverless", "lambda"],
    "Docker": ["docker", "container"],
    "Redis / queues": ["redis", "bullmq", "queue", "message broker", "job queue", "kafka"],
    "Git / CI": ["git", "github", "version control", "ci/cd", "continuous integration"],
    "Structured output": ["json", "schema", "structured output", "pydantic", "data model"],
    "Data pipelines": ["etl", "data pipeline", "pipeline", "data engineering", "spark", "airflow", "dbt", "data warehouse", "bigquery", "snowflake"],
    "Communication": ["communicat", "collaborat", "cross-functional", "stakeholder", r"\bpresent(ation|ing)?s?\b", "written", "verbal", "teamwork", "team player", "mentor", "explain", "customer", "client-facing", "customer-facing"],
    "Degree": ["bachelor", "master", "b.s", r"\bbs\b", "m.s", r"\bms\b", "msc", "bsc", "degree", "computer science", "mathematics", "quantitative", "related field", "equivalent experience", "equivalent practical"],
    "Problem solving": ["problem-solving", "problem solving", "analytical", "curious", "self-starter", "fast-paced", "ambiguity", "autonomous", "independently", "learn"],
    "Projects": ["coursework", "internship", "personal project", "side project", "personal work", "portfolio", "hackathon", "open source", "open-source", "github", "research experience", "research project"],
    "Healthcare / regulated": ["healthcare", "health", "medical", "clinical", "hipaa", "regulated"],
    "Testing": ["unit test", "testing", "test-driven", "tdd", "pytest", "code review", "software engineering best practices", "best practices", "clean code", "design patterns", "object-oriented", "oop"],
    "Linux / shell": ["linux", "unix", "bash", "shell", "command line", r"\bcli\b"],
    "MATLAB": ["matlab"],
    "Signal processing": ["signal processing", "imaging"],
}

# Things a JD often requires that are NOT on the ledger. Kept explicit so "has" never leaks.
# (label, regex)
NOT_HELD: list[tuple[str, str]] = [(w, r"(?<![a-z0-9+#.])" + re.escape(w) + r"(?![a-z0-9+#])") for w in [
    "c++", "rust", "golang", "java", "c#", ".net", "swift", "kotlin", "scala", "ruby", "php", "elixir", "haskell", "cuda",
    "verilog", "vhdl", "ios", "android", "react native", "flutter", "unity", "unreal", "salesforce", "sap", "servicenow",
    "workday", "mainframe", "cobol", "abap", "phd", "ph.d", "doctorate", "spark", "hadoop", "hive", "azure", "kafka",
    "terraform", "ansible", "security clearance", "clearance", "objective-c", "matlab/simulink", "simulink", "labview",
    "chef", "puppet", "post-training", "pre-training", "pretraining", "rlhf", "reinforcement learning", "distributed training",
    "inference optimization", "model serving", "vllm", "triton", "tensorrt", "publications", "first-author", "neurips", "icml", "iclr",
    "speech model", "diffusion", "recommendation system", "recommender", "ranking model", "ads ranking", "react native",
    "configuration management", "active directory", "okta", "servicenow", "jira administration", "founder", "founding engineer",
    "kubernetes", "k8s", "helm", "bare metal", "networking infrastructure", "distributed storage", "iam", "authentication", "authorization",
    "identity", "oauth", "saml", "sso", "flyte", "airflow", "dagster", "ray", "databricks", "pyspark", "streaming pipeline", "flink",
    "on-call", "oncall", "site reliability", "observability platform", "prometheus", "grafana",
    "speech", "stt", "asr", "text-to-speech", "audio", "computer vision models", "visual encoder", "generative computer vision",
]] + [
    ("go", r"\bgo\b(?=[ ,/)]+(and|or|,|programming|developer|experience|expertise|proficien|knowledge|skills|background|preferred|required|is|a plus|$))"),
    ("training LLMs", r"\b(pre-?)?train(ing|ed)?\b[^.;]{0,50}\b(large language models?|llms?|foundation models?|frontier models?|neural networks?|deep learning models?|transformer models?)"),
    ("training LLMs", r"\b(large language models?|llms?|foundation models?)\b[^.;]{0,30}\b(pre-?)?training\b"),
    ("go", r"\(go\)"),
]
SENIOR_WORDS = re.compile(r"\b(proven track record|extensive|deep (expertise|experience|ml|theory|knowledge|understanding)|extremely strong|expert(-level)?|significant experience|"
                          r"led|leading|lead\b|mentor(ed|ing|ship)?|architect(ed|ing)?|\d+\+? ?years|senior)\b")
ALTERNATIVES = re.compile(r"\b(or|one of|such as|for example|any of|etc|equivalent|similar)\b|e\.g\.")
EXAMPLES = re.compile(r"\((?:e\.g\.|such as|for example|including|like)[^)]*\)|\b(?:e\.g\.|such as|for example|including|like)\b[^.;:]*", re.I)
CORE_TOOLS = re.compile(r"\b(python|typescript|javascript|node\.?js|sql|postgres(ql)?|mysql|gcp|google cloud|aws|docker|langchain|langgraph|"
                        r"pytorch|scikit|sklearn|pandas|numpy|flask|nestjs|redis|pinecone|tableau|power bi|matlab|lora|peft|sentence-transformers)\b", re.I)


def _hit(alias: str, text: str) -> bool:
    if alias.startswith("\\b"):
        return re.search(alias, text) is not None
    return re.search(r"(?<![a-z0-9])" + re.escape(alias), text) is not None  # prefix match: "eval" hits "evaluation"


def _span_with(text: str, pat: str) -> str:
    """The clause (parenthetical if inside one, else between ; : or sentence end) containing the first match of pat."""
    m = re.search(pat, text)
    if not m:
        return text
    i = m.start()
    open_ = text.rfind("(", 0, i)
    close = text.find(")", i)
    if open_ != -1 and close != -1 and text.find(")", open_) >= i:
        return text[open_:close + 1]
    start = max(text.rfind(c, 0, i) for c in ";:.")
    end = min([x for x in (text.find(";", i), text.find(":", i), text.find(".", i)) if x != -1] or [len(text)])
    return text[start + 1:end]


def classify(requirement: str) -> tuple[bool, str]:
    """(has, why). A requirement is held only when it names a ledger-backed skill and nothing from NOT_HELD.
    A NOT_HELD term is forgiven when it sits in an examples list ("e.g., Spark, Ray") or in an alternatives list
    that also names a tool the ledger supports ("Python, Go, or Rust")."""
    t = " " + requirement.lower().strip() + " "
    # A range states a minimum, not an extra seniority requirement at its
    # upper end (e.g. "2–4+ years" requires two years, not four).
    t = re.sub(r'\b(\d+)\s*(?:[-–—]|to)\s*\d+\s*\+?\s*(?=years?\b)', r'\1 ', t)
    if (m := SENIOR_WORDS.search(t)) and not re.search(r"\b[0-3]\+? ?years\b", t):
        return False, f"senior-level wording: {m.group(0)}"
    held_as = None
    for canonical, aliases in ALIASES.items():
        if any(_hit(a, t) for a in aliases):
            held_as = canonical
            break
    examples = " ".join(EXAMPLES.findall(t))
    for label, pat in NOT_HELD:
        m = re.search(pat, t)
        if not m:
            continue
        if held_as and re.search(pat, examples) and label not in ("phd", "ph.d", "doctorate"):
            continue  # named only as an example of the kind of thing wanted
        span = _span_with(t, pat)
        if held_as and span.startswith("(") and CORE_TOOLS.search(span) and label not in ("phd", "ph.d", "doctorate"):
            continue  # "containerization (Docker, Kubernetes)": a category illustrated with tools, one of which is held
        degree_alt = label in ("phd", "ph.d", "doctorate") and re.search(r"\b(master|bachelor|ms|bs|msc|bsc|m\.s|b\.s)\b", t)
        if held_as and ALTERNATIVES.search(span) and (degree_alt or (label not in ("phd", "ph.d", "doctorate") and CORE_TOOLS.search(span))):
            return True, f"{held_as} (one of the alternatives)"
        return False, f"not held: {label}"
    if held_as:
        return True, held_as
    return False, "no ledger evidence"


def coverage(requirements: list[str]) -> tuple[list[str], list[str]]:
    has, missing = [], []
    for r in requirements:
        ok, why = classify(r)
        (has if ok else missing).append(f"{r.strip()} → {why}" if ok else r.strip())
    return has, missing
