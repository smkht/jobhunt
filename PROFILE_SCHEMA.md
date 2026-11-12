# Profile schema (`data/profile.json`)

The profile is the **only source of facts** for CVs, cover letters and interview prep. The AI may reorder, trim and reword these facts but never invents new ones.
A complete fictional example is in `examples/profile.example.json`.

```json
{
  "name": "Jane Example",
  "contact": ["jane@example.com", "+00 000 000 000", "Springfield", "linkedin.com/in/jane-example"],
  "headline_default": "Operations Manager",
  "stats": [["10 yrs", "operations experience"], ["30%", "cost reduction"]],
  "education": [["Master's in Business Administration", "Example University · 2008–2010"]],
  "credentials": ["Certified Project Manager (2018)"],
  "skills": ["Project management", "Vendor management"],
  "languages": ["English — Native", "Spanish — Beginner"],
  "jobs": [
    {"title": "Operations Manager", "org": "Acme Corp, Springfield", "dates": "2019 – present",
     "bullets": ["Cut delivery time by 30% by redesigning the dispatch process.", "..."]}
  ],
  "notes_for_tailoring": "Free text for the AI: how to position this candidate, what never to claim."
}
```

| Field | Type | Meaning |
|---|---|---|
| `name` | string | Used in the CV header, file names (`Jane_Example_CV.pdf`) and as the letter signature (first name). |
| `contact` | list of strings | Shown on one line under the name. |
| `headline_default` | string | Fallback CV headline. |
| `stats` | list of `[value, label]` | Optional "Highlights" line on the CV. |
| `education` | list of `[degree, school · years]` | |
| `credentials` | list of strings | Certifications, licences, scholarships. |
| `skills` | list of strings | Used for fit checks and the Core Skills section. |
| `languages` | list of strings | Shown on the CV. What the candidate can work in is set separately by `languages_spoken` in `data/config.json`. |
| `jobs` | list of objects | **Newest first.** See below. |
| `notes_for_tailoring` | string | Optional instructions for the AI (positioning, things never to claim). |

## Jobs

Each job: `title`, `org`, `dates`, `bullets` (list of facts, ideally with a number) and an optional `section`:

| `section` | Where it appears |
|---|---|
| *(missing)* | Normal **Professional Experience**, in the order listed. |
| `"extra"` | After the main experience: early, short or unrelated roles. |
| `"projects"` | A **Projects** block (side projects, self-directed technical work), shown only when the posting mentions tools, software, data or AI. |
| `"research"` | Listed under **Education** (for example doctoral research); bullets appear when the posting is research or legal related. |

The tailoring step refers to jobs by their index in this list, so keep the order stable after drafts exist.

## Related files in `data/` (all optional)

* `config.json`: your search preferences; overrides `defaults/config.json`. Useful keys: `home_city`, `home_country`, `languages_spoken`, `title_keywords`, `title_exclude`, `location_ok`, `location_bad`, `onsite_ok_cities`, `avoid_cities`, `region_words`, `experience_years`, `salary_bands`, `timezone_note`, `contractor_ok`, `contacts_since`, `cover_letter_style`.
* `answers.json`: standing answers to common application questions (`{"items": [{"q", "a", "needs_input"}]}`; `{company}`, `{role}`, `{why_us}`, `{summary}` are filled in).
* `stories.json`: story bank for interview prep (`{"answer_rules": [...], "stories": [...]}`).
* `network.json`: referral contacts (`{"contacts": [{"contact", "match": ["company"], ...}]}`).
* `outreach_templates.json`: message templates (`{"templates": [{"id", "use", "text"}]}`, `{company}` / `{role}` filled in).
