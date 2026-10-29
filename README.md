# jobworker-engine

An autonomous job application worker. It sources postings from public ATS boards, scores them against a saved profile, tailors a resume from a verified fact ledger, and fills and submits Ashby, Greenhouse, Lever and Talemetry forms in a headless browser. Airtable stores job history, settings and unanswered questions. It is designed to run on GitHub Actions with nothing running on your own machine.

This repository contains **code only**. Everything personal (profile, answer bank, fact ledger, resumes, run state and logs) lives in a separate **private** repository and is fetched at run time.

## How the two repos fit together

| | Public: this repo | Private: your data repo |
|---|---|---|
| Contents | engine code, tests, workflows | `worker/data/` on `main`; run state on branch `handoff-data` |
| Runs | scheduled workflows on free public runners | nothing |
| Secrets | none in the repo | none in the repo |

Each workflow checks out the private repo, copies `worker/data/` over the engine's empty `worker/data/`, restores run state from `handoff-data`, runs the worker, and pushes new state back to `handoff-data`.

**Logs are private.** The worker's output is written to `logs/` on the private repo's `handoff-data` branch. The public Actions log shows only the command and its exit code, so company names, roles and answers are never published.

## Setup

1. Create a private repo for your data. Put your profile files under `worker/data/` (see `worker/data/README.md`) and create a `handoff-data` branch for state.
2. Create a fine-grained personal access token limited to that one private repo with **Contents: Read and write**.
3. Add these Actions secrets to this repo: `DATA_REPO` (`owner/private-repo`), `DATA_REPO_TOKEN`, `AIRTABLE_TOKEN`, `AIRTABLE_BASE_ID`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `CLAUDE_CODE_OAUTH_TOKEN`, and optionally `APOLLO_API_KEY`.
4. Run the **JobWorker Cloud Run** workflow with the `doctor` command, then `status`, then `dry-run`, before enabling live cycles.

## Phone swipe deck

`docs/index.html` is a static page (published with GitHub Pages) for approving jobs by swiping. Mid-fit roles (score between the Airtable Settings keys `review_floor`, default 55, and `fit_threshold`, default 70) are parked as `Scored` rows. Swipe right sets the row to `Ready`, and the next worker cycle tailors a resume and submits it; swipe left sets `Skipped`. High-fit roles still follow `auto_submit`. Open the Pages URL on your phone, enter an Airtable token (scopes `data.records:read` and `data.records:write` on your base) and base ID once, then use Add to Home Screen. The token stays in that browser only. A daily right-swipe limit (default 30) is enforced in the page, and `review_queue_max` (default 150) limits how many cards wait.

## Operations

From the **JobWorker Cloud Run** workflow, choose a command: `status`, `doctor`, `dry-run`, `ready`, `cycle`, `digest`, `review`, `sweep`, `run`.

Schedule (America/Chicago): hourly cycle at :30, a daily digest, reply and follow-up sweeps, and a weekly review. `JobWorker Send Pending Recruiter Emails` runs on its own schedule inside the configured send window. `Keepalive` makes one commit a month so GitHub does not disable scheduled workflows after 60 days of inactivity.

## Behavior and limits

- Unattended submission is controlled by the Airtable Settings key `auto_submit`. Set it to `false` to stop submissions.
- A CAPTCHA, an unsupported portal or an unknown required question parks that one application; the rest of the batch continues. The worker never invents a required answer.
- Daily new-application limits, per-company allowances and a cooldown between applications to the same employer are enforced from saved settings.
- Free-text answers come only from the verified ledger and saved preferences.

## Development

Python 3.11+: `pip install -e '.[dev]'`, `playwright install chromium`, `pytest`. The tests in this repo use local fixtures and make no real submissions or network calls. Tests that need a real profile live in the private repo.

Never commit anything under `worker/data/` here: it is git-ignored on purpose.
