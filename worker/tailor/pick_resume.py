"""Choose one of the six base resumes. Model output must be a known key; otherwise keyword map."""
from __future__ import annotations

import re

from worker.llm.ollama import generate_json as _generate_json
from worker.models import Posting

generate_json = _generate_json

KEYS = {
    "MeridianLink_AI_Engineer_II": "AI Engineer: LLM apps, RAG, evaluation frameworks, secure deployment (enterprise/fintech)",
    "Stripe_AI_Engineer": "AI Engineer: agents, LLM-powered internal tools, workflow automation (product/platform companies)",
    "LangChain_FullStack_Engineer_Evals": "Full-stack engineer: LangGraph, evals/observability tooling, Python + TypeScript",
    "ScaleAI_Frontier_Agents_Engineer": "Applied AI engineer: production agents, retrieval/memory, quantitative evaluation (AI labs/startups)",
    "Databricks_AI_FDE": "Forward-deployed / customer-facing GenAI engineer: RAG, multi-agent, fine-tuning, evaluation",
    "KirklandEllis_AI_Engineer_II": "AI Engineer II in a regulated/legal/enterprise setting: RAG, evaluation, security & responsible AI",
}
KEYWORD_MAP = [
    (r"forward deploy|fde|customer engineer|solutions engineer|deployment strateg", "Databricks_AI_FDE"),
    (r"full[- ]?stack|typescript|frontend|observab|langsmith|evals? platform", "LangChain_FullStack_Engineer_Evals"),
    (r"agent|applied ai|research engineer|frontier", "ScaleAI_Frontier_Agents_Engineer"),
    (r"legal|law|compliance|security|regulated|responsible ai|governance|health", "KirklandEllis_AI_Engineer_II"),
    (r"internal tools|automation|workflow|productivity|platform", "Stripe_AI_Engineer"),
]
SCHEMA = {"type": "object", "properties": {"resume": {"type": "string", "enum": list(KEYS)}}, "required": ["resume"]}


def keyword_pick(p: Posting) -> str:
    t = (p.title + " " + p.jd_text[:3000]).lower()
    for pat, key in KEYWORD_MAP:
        if re.search(pat, t):
            return key
    return "MeridianLink_AI_Engineer_II"


def pick(p: Posting) -> str:
    menu = "\n".join(f"- {k}: {v}" for k, v in KEYS.items())
    prompt = (f"Pick the single best base resume for this job. Return JSON {{\"resume\": <key>}} using exactly one key below.\n{menu}\n\n"
              f"Job: {p.title} at {p.company}\n{p.jd_text[:4000]}")
    try:
        out = generate_json(prompt, SCHEMA)
        key = out.get("resume") if isinstance(out, dict) else None
    except Exception:  # noqa: BLE001
        key = None
    return key if key in KEYS else keyword_pick(p)
