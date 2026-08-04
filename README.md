# Kohort Technical Interview

Welcome! Please follow the setup below **before the session starts** so we can
jump straight into the interesting part.

## Setup (5 minutes)

```bash
git clone <this-repo>
cd kohort-tech-interview
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
python3 starter.py
```

If `starter.py` prints the row counts of five data files, you're ready.

**No local Python?** Open this repo in a GitHub Codespace (green "Code" button →
Codespaces → "Create codespace") — everything is preinstalled. Run
`python3 starter.py` in the terminal to verify.

## During the session

- Use any editor or notebook you like.
- **AI assistants (Claude etc.) are allowed and encouraged** — keep the assistant
  visible on your shared screen; how you work with it is part of the session.
- Task details are in `candidate_brief.md` — we'll walk through it together at
  the start.
- At the end, please commit and push your work to a branch named after you:

```bash
git checkout -b <your-first-name>
git add -A && git commit -m "interview session"
git push -u origin <your-first-name>
```

Any setup problems, contact us before the session — don't burn interview time on
pip issues.
