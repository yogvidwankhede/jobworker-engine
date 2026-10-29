# worker/data (intentionally empty in the public repo)

At run time the workflows copy the private data repo's `worker/data/` here. Expected files:

`profile.yaml`, `answers.yaml`, `authorization.yaml`, `companies.yaml`, `hooks.yaml`, `ledger.yaml`,
`screening_answers.md`, `screening_responses.yaml`, `taglines.yaml`, `resume_links.yaml`, `cover_letter.tex`,
`resumes/` (LaTeX sources and `preamble.tex`) and `templates/` (recruiter email templates).

These files hold personal information. They must only ever exist in the private repo.
