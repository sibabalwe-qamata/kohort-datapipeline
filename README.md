# Kohort Data Engineering Take-Home

## What this is

A take-home for the Data Engineer role at Kohort, followed by a 60-minute
conversation about what you built. Most people spend 4–6 hours. Please stop at 8
and write what you would do next in `NOTES.md`; we grade what is there, not what
is promised.

The task itself is in `ASSIGNMENT.md`. Read it fully before writing code.

## Setup

Create your own copy of this repository first (see **Submission** below), clone
it, then follow the steps for your platform.

**macOS / Linux:**

```bash
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
python3 check_env.py
```

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1   # optional but recommended
pip install -r requirements.txt
python check_env.py
```

**Windows (Command Prompt):**

```bat
python -m venv .venv
.venv\Scripts\activate.bat   # optional but recommended
pip install -r requirements.txt
python check_env.py
```

Windows notes:

- If PowerShell blocks the activate script ("execution of scripts is disabled"),
run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` once,
or use Command Prompt instead.
- If `python` opens the Microsoft Store, install Python from
[python.org](https://www.python.org/downloads/) (tick "Add Python to PATH"), or
use the `py` launcher (`py -m venv .venv`).
- In Git Bash, use `source .venv/Scripts/activate`.

If `check_env.py` prints the row counts of five data files, you're ready.

## Rules

- Use any tools you would use at work, including AI assistants. In the follow-up
we will ask you to explain and change any part of the code live; code you cannot
explain counts against you, using AI does not.
- Your pipeline will also be run on a second dataset with the same schema and
date range but different values and a few extra edge cases, so do not hard-code
anything from this one.
- The follow-up includes one short SQL exercise done without an assistant.
- If the spec is ambiguous, pick an interpretation, state it in `NOTES.md`, and
carry on; that is what we do with studios too.

## Submission

Click **Use this template → Create a new repository** on GitHub and make it
**Private**. Commit as you go — we read the history (a single squash is not
disqualifying, but a story helps). Commit `output/` from a default run. When
done, add **@TODO-github-handle** as a collaborator and email the repo link to
**michelle@kohort.io**. If you would rather not use GitHub, email a zip of the
repo **including the `.git` folder** instead. Please do not fork publicly or
publish solutions.

## What happens next

We run our grader on your repo within two working days, then schedule the
60-minute follow-up within a week. You hear the outcome either way.

## Questions

Email michelle@kohort.io.
