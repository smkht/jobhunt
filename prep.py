#!/usr/bin/env python3
"""Interview prep for one job: python3 prep.py <job id>  ->  <application folder>/interview_prep.md
Uses the same AI backend as tailor.py (claude CLI or ANTHROPIC_API_KEY; TAILOR_MOCK=1 for an offline stub). Facts only from profile.json + stories.json."""
import json, os, sys
from pathlib import Path
import tailor as T

import paths

PROMPT = """You prepare {name} for a job interview. Facts about the candidate (the ONLY source; never invent):
PROFILE: {profile}
STORY BANK (real experiences, with gaps the candidate still has to fill): {stories}

JOB: {title} at {company}
{desc}

Known gaps vs this posting: {gaps}
Every answer outline must contain one number, the candidate's personal role, and one obstacle.

Return ONLY JSON:
{{"opening_pitch": "60-second 'tell me about yourself' tuned to this role, first person, <=120 words, facts only",
 "questions": [{{"q": "likely question", "why": "what they test", "story_id": "best story id from the bank or ''", "outline": "3-4 bullet answer outline using only real facts, as one string with newlines"}}] (8-10 items, mix of competency, role-specific and the weak spots),
 "gap_answers": [{{"gap": str, "honest_framing": "what the candidate can truthfully say"}}],
 "ask_them": [str] (5 sharp questions to ask the interviewer, specific to the posting),
 "red_flags_to_avoid": [str] (3-4 items)}}"""


def mock():
    return {"opening_pitch": "MOCK pitch.", "questions": [{"q": "MOCK question", "why": "-", "story_id": "", "outline": "- point"}],
            "gap_answers": [], "ask_them": ["MOCK?"], "red_flags_to_avoid": ["-"]}


def render(job, p):
    md = [f"# Interview prep: {job['title']} - {job['company']}", "", f"Posting: {job.get('url', '')}", "", "## 60-second opening", p["opening_pitch"], "", "## Likely questions"]
    for i, q in enumerate(p["questions"], 1):
        md += [f"### {i}. {q['q']}", f"*Tests: {q['why']}* | Story: `{q.get('story_id') or '-'}`", "", q["outline"], ""]
    md += ["## Gaps: how to answer honestly"] + [f"- **{g['gap']}**: {g['honest_framing']}" for g in p.get("gap_answers", [])]
    md += ["", "## Questions to ask them"] + [f"- {x}" for x in p["ask_them"]] + ["", "## Avoid"] + [f"- {x}" for x in p.get("red_flags_to_avoid", [])]
    stories = paths.read_json("stories.json", {})
    if stories.get("answer_rules"):
        md += ["", "## Answer rules"] + [f"- {x}" for x in stories["answer_rules"]]
    return "\n".join(md) + "\n"


def prep(job):
    T.refresh()
    d = T.app_dir(job)
    draft = json.loads((d / "draft.json").read_text()) if (d / "draft.json").exists() else {}
    prompt = PROMPT.format(name=T.P["name"], profile=json.dumps({k: v for k, v in T.P.items() if k != "notes_for_tailoring"}, ensure_ascii=False),
                           stories=json.dumps(paths.read_json("stories.json", {}), ensure_ascii=False), title=job["title"], company=job["company"], desc=job["desc"][:6000],
                           gaps="; ".join(draft.get("gaps", [])) or "unknown")
    p = mock() if T.backend() == "mock" else T.ask_llm_raw(prompt)
    d.mkdir(parents=True, exist_ok=True)
    (d / "interview_prep.md").write_text(render(job, p))
    print("OK", d / "interview_prep.md")
    return d / "interview_prep.md"


if __name__ == "__main__":
    prep(T.load_job(sys.argv[1:]))
