"""Auto-drafts short, personalized outreach messages for the strongest surfaced matches.

Not a cover letter — a LinkedIn-DM/recruiter-email opener, matching the humanized,
no-em-dash style already used for this user's LinkedIn outreach (see resume.md for
the exact background facts pulled into each draft: name, degree, GPA, standout project).
"""
from datetime import datetime
from pathlib import Path

NAME = "Adi Karthikeya"
DEGREE_LINE = "an M.S. in Data Science from the University of Houston (3.9 GPA)"

# One standout, resume-backed proof point per role family, so the draft references
# something concretely relevant instead of a generic "I'm a great fit" line.
_PROOF_POINTS = {
    "data_engineer": ("built ingestion and transformation pipelines across relational "
                       "(PostgreSQL), graph (Neo4j), and vector (ChromaDB) stores, including "
                       "one that lifted retrieval accuracy from 10% to 92.5%"),
    "data_scientist": ("built a facies-classification pipeline that scored 0.8024 accuracy "
                        "in a national Kaggle-style competition during an NSF-funded data "
                        "science fellowship"),
    "data_analyst": ("shipped Tableau dashboards and QGIS maps turning raw EIA and Census "
                      "records into decision-ready reporting for stakeholders"),
    "ai_engineer": ("built a RAG pipeline that lifted retrieval accuracy from 10% to 92.5% "
                     "across 40 adversarial cases with a zero-hallucination validation layer"),
    "ml_engineer": ("engineered a 52-feature pipeline over 60,000 profiles and shipped a "
                     "SHAP-explainable Streamlit dashboard ranking personalized recommendations"),
    "software_engineer": ("built and shipped production-grade agentic systems end to end, "
                           "from ChromaDB/Neo4j-backed pipelines to FastAPI services"),
}
_DEFAULT_PROOF = ("built and shipped end-to-end data pipelines and ML/AI systems, from "
                   "SQL/ETL work to production-grade dashboards and agentic tools")


def _draft(job: dict) -> str:
    title = job.get("title", "this role")
    company = str(job.get("company", "")).split(" (via")[0]
    proof = _PROOF_POINTS.get(job.get("role_family", ""), _DEFAULT_PROOF)
    keywords = job.get("top_keywords_list") or []
    keyword_line = f" My background lines up closely on {', '.join(keywords[:4])}." if keywords else ""
    return (
        f"Hi, I'm {NAME}. I have {DEGREE_LINE} and saw the {title} opening at {company}. "
        f"I {proof}.{keyword_line} I'd love to talk about how I could contribute to your "
        f"team, here's my resume if useful: [attach/link]."
    )


def generate_drafts(jobs: list, out_dir: Path, run_stamp: str, min_score: float = 60.0,
                     top_n: int = 10) -> dict:
    """Writes one draft per strong match (score >= min_score, capped at top_n) to a
    markdown file. Only genuinely strong matches get a draft — this is meant to save
    time on the roles worth reaching out for, not to spam every surfaced posting."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ranked = sorted(jobs, key=lambda j: j.get("match_score", 0.0), reverse=True)
    picked = [j for j in ranked if j.get("match_score", 0.0) >= min_score][:top_n]

    path = out_dir / f"outreach_drafts_{run_stamp}.md"
    if not picked:
        path.write_text(f"# Outreach drafts\n\nNo surfaced posting scored >= {min_score} this run.\n")
        return {"path": path, "count": 0}

    gen = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"# Outreach drafts — {len(picked)} strong matches (score >= {min_score})",
             f"Generated {gen}\n"]
    for j in picked:
        company = str(j.get("company", "")).split(" (via")[0]
        lines.append(f"## {company} — {j.get('title', '')} (score {j.get('match_score', 0):.1f})")
        lines.append(f"Apply: {j.get('apply_link', '')}\n")
        lines.append(_draft(j))
        lines.append("")
    path.write_text("\n".join(lines))
    return {"path": path, "count": len(picked)}
