"""
Parses resume.md (or, as fallback, base_resume.tex directly) into weighted sections
for match_scorer.py. Handles the LaTeX-passthrough case markitdown produces for .tex
input by stripping LaTeX commands down to plain text.

Sections extracted: summary, skills, experience_projects, education. If a summary
section is missing or generic (< 10 content words), scoring must still work off the
other sections — enforced in match_scorer.py, not here.
"""
import re
import subprocess
from pathlib import Path

SECTION_HEADERS = {
    "summary": ["professional summary", "summary", "objective"],
    "skills": ["skills", "technical skills"],
    "experience_projects": ["experience", "projects", "work experience", "project experience"],
    "education": ["education"],
}

LATEX_STRIP_COMMANDS = re.compile(r"\\(documentclass|usepackage|input|pagestyle|fancyhf|fancyfoot|"
                                   r"renewcommand|setlength|urlstyle|raggedbottom|raggedright|"
                                   r"setcolsep|titleformat|pdfgentounicode|newcommand|labelitemii)"
                                   r"(\[[^\]]*\])?(\{[^{}]*\})*", re.MULTILINE)
LATEX_ENV = re.compile(r"\\(begin|end)\{[^{}]*\}(\[[^\]]*\])?")
LATEX_CMD_WITH_ARG = re.compile(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?\{([^{}]*)\}")
LATEX_CMD_BARE = re.compile(r"\\[a-zA-Z]+\*?")
LATEX_COMMENT = re.compile(r"(?<!\\)%.*")
LATEX_HREF = re.compile(r"\\href\{[^{}]*\}\{([^{}]*)\}")


def _strip_latex(text: str) -> str:
    text = LATEX_COMMENT.sub("", text)
    text = LATEX_HREF.sub(r"\1", text)
    text = LATEX_STRIP_COMMANDS.sub("", text)
    text = LATEX_ENV.sub("", text)
    # repeatedly unwrap nested \cmd{...} -> inner text, since regex can't nest arbitrarily
    for _ in range(6):
        new_text = LATEX_CMD_WITH_ARG.sub(r"\2", text)
        if new_text == text:
            break
        text = new_text
    text = LATEX_CMD_BARE.sub("", text)
    text = text.replace("\\\\", " ").replace("\\%", "%").replace("\\&", "&")
    text = re.sub(r"[{}]", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def sync_resume_md(tex_path: Path, md_path: Path) -> None:
    """Regenerate resume.md from base_resume.tex — call whenever the .tex changes."""
    raw = tex_path.read_text()
    cleaned = _strip_latex(raw)
    md_path.write_text(cleaned)


def _find_section_bounds(lines, header_aliases):
    for i, line in enumerate(lines):
        low = line.strip().lower().strip("#: ")
        if low in header_aliases:
            return i
    return None


def parse_resume_sections(md_path: Path) -> dict:
    """Returns {section_name: plain_text} for skills/summary/experience_projects/education.
    Falls back gracefully: any section not found returns an empty string, never raises,
    so scoring can proceed off whatever sections do exist.
    """
    if not md_path.exists():
        raise FileNotFoundError(
            f"{md_path} not found. Run `python3 cli.py sync-resume` first to generate it "
            f"from base_resume.tex."
        )
    text = md_path.read_text()
    lines = text.split("\n")

    # Locate all section start indices, in whatever order they appear.
    found = {}
    for name, aliases in SECTION_HEADERS.items():
        idx = _find_section_bounds(lines, aliases)
        if idx is not None:
            found[name] = idx

    ordered = sorted(found.items(), key=lambda kv: kv[1])
    sections = {name: "" for name in SECTION_HEADERS}
    for pos, (name, start_idx) in enumerate(ordered):
        end_idx = ordered[pos + 1][1] if pos + 1 < len(ordered) else len(lines)
        body = "\n".join(lines[start_idx + 1:end_idx]).strip()
        # experience + projects may both map to experience_projects — concatenate
        if sections[name]:
            sections[name] += "\n" + body
        else:
            sections[name] = body

    return sections


def full_resume_text(sections: dict) -> str:
    return "\n\n".join(v for v in sections.values() if v)
