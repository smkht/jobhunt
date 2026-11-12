# jobhunt: guide for Claude (or another coding agent)

The user opened this folder in Claude because they want to use jobhunt, or because something in it failed.
Your job: get them to a working board of fresh, well-matched jobs with tailored CVs, fixing small problems along the way.
They may not be technical. Explain in plain words, do the work yourself, and ask only for decisions that are theirs.

## What the app is
A local, review-first job-search assistant (Python 3.9+, standard library only):
- `search.py` finds fresh postings from public sources (LinkedIn public pages, company ATS boards, remote job boards), filters them and scores the fit against the user's profile.
- `tailor.py` drafts an ATS-matched CV and a short cover letter per role, using ONLY facts from `data/profile.json`.
- `app.py` is the dashboard (board, apply kit, tracker, funnel) at http://127.0.0.1:8765. `onboarding.py` is the first-run setup wizard at `/setup`.
- `daily.py` = search + draft the top matches. `install_schedule.sh` runs it on a schedule (macOS).

## Ground rules
- Never submit applications, send messages or create accounts for the user. The app prepares; the user applies.
- Everything personal lives in `data/` (git-ignored): profile, config, answers, tracker, drafts. Never commit it, never paste its contents into issues, logs or anything public.
- Never invent facts for the profile. Experience, numbers and employers come from the user's CV or from the user in chat.
- Don't change the shared defaults (`defaults/config.json`) for one person's taste; per-user settings go in `data/config.json`.

## Start the app
```bash
python3 app.py            # opens http://127.0.0.1:8765, wizard on first run
PORT=8766 python3 app.py  # if 8765 is taken
TAILOR_MOCK=1 python3 app.py   # no AI at all: test the whole flow with stand-in drafts
```
If the user can't use the browser wizard, you can do the setup with them in chat and write the same files the wizard writes
(see `onboarding.save_all(profile, prefs)`: it builds `profile.json`, `config.json`, `answers.json`, `preferences.json` from two dicts).
`PROFILE_SCHEMA.md` describes the profile; `examples/` has a fictional sample.

## The wizard, step by step
1. Connect an AI: logged-in `claude` CLI, or an Anthropic API key (saved in `data/.env`).
2. Your CV: PDF / DOCX / DOC / RTF / TXT upload or pasted text -> `onboarding.extract_text`.
3. Check your profile: the AI draft (`parse_cv`) plus "Other experience" (side skills, hobbies, household work; used only when a posting asks for them).
4. What you want: titles, words to avoid, work mode, regions, languages. Pre-filled from the CV by `suggest_prefs`.
5. Answers and sources: typical pay for the target roles (`typical_pay`, an AI estimate) and the user's expectation.
   Pay never hides a role: at/above the expectation ranks higher, below is flagged "for your review" (`salary.compare`, used in `search.py`).
Each Next/Back saves `data/setup_draft.json`, so a reload or crash resumes where the user was.

## When something fails
| Symptom | Likely cause | Fix |
|---|---|---|
| "The Claude CLI failed" / "OAuth session expired" | CLI login expired | Ask the user to run `claude` in Terminal and `/login` (you can't log in for them), or add an API key in step 1. |
| "Could not read this PDF" | Scanned/image PDF, or no text tools | macOS reads PDFs natively; elsewhere `brew install poppler` / `apt install poppler-utils`. Otherwise ask for a .docx or pasted text. |
| Port already in use | Another copy is running | `PORT=8766 python3 app.py`, or stop the old one. |
| Board stays empty after a search | Titles too narrow, or a source changed | Read `data/out/run.log` (drop reasons are listed). Widen titles in Settings or `/setup`; a source returning 0 for days may need a parser fix in `search.py`. |
| Drafting fails | AI backend problem | Same as the first row; `TAILOR_MOCK=1` shows whether the rest of the pipeline works. |
| Wizard looks wrong after a code change | The server caches the page | Restart `app.py`. |
| Funnel or numbers look off | Status set by hand | Statuses live in `data/tracker.csv`; history in `data/out/status_history.csv` (append-only). |

Before changing code: reproduce, fix the smallest thing, and test with `TAILOR_MOCK=1` plus a throwaway data folder
(`JOBHUNT_DATA=/tmp/jh-test python3 app.py`) so the user's real data is never touched. Tell the user what you changed and why.

## Handy commands
```bash
python3 search.py          # search only
python3 daily.py           # search + draft top matches
python3 tailor.py <job-id> # draft one role
./install_schedule.sh      # run daily.py on a schedule (macOS); `remove` to undo
```
