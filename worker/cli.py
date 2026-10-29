"""Command-line entry points: doctor | run | sweep | review | dry-run."""
from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(add_completion=False, help="Autonomous cloud job-search worker")


def _cfg(env: Path | None):
    from worker.config import load

    return load(env)


@app.command()
def doctor(env: Path | None = typer.Option(None, help="Path to .env"),
           skip_inference: bool = typer.Option(False, help="Check infrastructure without consuming model quota"),
           inference_only: bool = typer.Option(False, help="Probe the cloud model without repeating infrastructure checks"),
           application_only: bool = typer.Option(False, help="Check application dependencies without depending on email or model availability")):
    """Check every credential and dependency; print OK/MISSING per line."""
    from worker.doctor import checks, inference_check

    results = [inference_check(_cfg(env))] if inference_only else checks(_cfg(env), check_inference=not skip_inference, application_only=application_only)
    for name, ok, note in results:
        typer.echo(f"{'OK     ' if ok else 'MISSING'}  {name}{('  (' + note + ')') if note else ''}")
    if any(not ok for name, ok, _ in results if "optional" not in name):
        raise typer.Exit(1)


@app.command()
def status(env: Path | None = typer.Option(None)):
    """Cloud configuration, tracker counts, and unique unanswered questions; never prints credentials."""
    import json
    from collections import Counter

    from worker.config import parse_settings, apply_saved_company_policy
    from worker.local_time import now
    from worker.records.airtable import AirtableStore

    store = AirtableStore(_cfg(env))
    settings = parse_settings(store.settings())
    import yaml
    profile = yaml.safe_load((_cfg(env).data_dir / 'profile.yaml').read_text())
    apply_saved_company_policy(settings, profile.get('preferences', {}))
    jobs = store.get_jobs()
    questions = sorted({q.strip() for j in jobs if j.status == "Needs you"
                        for q in (j.questions_for_me or "").split(";") if q.strip()})
    typer.echo(json.dumps({"local_time": now().isoformat(), "timezone": "America/Chicago",
                          "settings": settings.model_dump(mode="json"),
                          "job_counts": dict(Counter(j.status for j in jobs)),
                          "job_issues": [{"company": j.company, "role": j.role, "status": j.status,
                                          "questions": j.questions_for_me, "notes": j.notes}
                                         for j in jobs if j.status in {"Needs you", "Error", "Submitting"}],
                          "unanswered_questions": questions}, indent=2))


@app.command()
def run(env: Path | None = typer.Option(None), dry_run: bool = typer.Option(False, "--dry-run"),
        send_window: str | None = typer.Option(None, "--send-window", help='One-off window override, e.g. "21:00-23:59"'),
        ready_only: bool = typer.Option(False, "--ready-only", help="Submit queued Ready/Ready to submit rows; source nothing")):
    """Daily pipeline: source, score, tailor, apply, email, digest."""
    if dry_run:
        from worker.pipeline import run as _run
        rep = _run(_cfg(env), dry_run=True, send_window=send_window, ready_only=ready_only)
    else:
        from worker.cycle import run as _run
        rep = _run(_cfg(env), send_window=send_window, ready_only=ready_only)
    typer.echo(rep.summary())


@app.command()
def cycle(env: Path | None = typer.Option(None),
          model_unavailable: bool = typer.Option(False, help="Continue selected work; defer new model-dependent jobs")):
    """Hourly unattended cloud run with a shared daily application allowance."""
    from worker.cycle import run as _run
    typer.echo(_run(_cfg(env), model_available=not model_unavailable).summary())


@app.command("digest")
def consolidated_digest(env: Path | None = typer.Option(None)):
    """Email one consolidated activity and exception digest."""
    from worker.cycle import digest
    typer.echo(digest(_cfg(env)))


@app.command("dry-run")
def dry_run(env: Path | None = typer.Option(None)):
    """Everything except submit/send; digest printed to stdout."""
    from worker.pipeline import run as _run

    typer.echo(_run(_cfg(env), dry_run=True).summary())


@app.command()
def sweep(env: Path | None = typer.Option(None)):
    """Replies, bounces, interviews, follow-ups due."""
    from worker.sweep import run as _sweep

    typer.echo(_sweep(_cfg(env)).summary())


@app.command()
def review(env: Path | None = typer.Option(None)):
    """Weekly review email."""
    from worker.review import run as _review

    typer.echo(_review(_cfg(env)))


@app.command("handoff")
def handoff(env: Path | None = typer.Option(None), day: str | None = typer.Option(None, help="YYYY-MM-DD, default today")):
    """List today's filled applications waiting to be submitted in Chrome (JSON, one per line)."""
    import json

    from worker.apply.handoff import load_day
    from worker.records.airtable import AirtableStore

    cfg = _cfg(env)
    waiting = {j.id for j in AirtableStore(cfg).get_jobs("Ready to submit")}
    for item in load_day(cfg, day):
        if item["job_id"] in waiting:          # already submitted/parked rows are never refilled
            typer.echo(json.dumps(item))


@app.command("park")
def park(job_id: str, reason: str, env: Path | None = typer.Option(None)):
    """A hand-off submit that did not confirm: park the row as Needs you with the reason (no email is sent)."""
    from worker.records.airtable import AirtableStore

    AirtableStore(_cfg(env)).update_job(job_id, status="Needs you", questions_for_me=reason[:500], next_action="")
    typer.echo(f"parked {job_id}")


@app.command("mark-applied")
def mark_applied(job_id: str, env: Path | None = typer.Option(None)):
    """After a Chrome submit confirmed: set the row Applied and queue its recruiter email (sent later, delayed - never here)."""
    from worker.handoff_done import mark_applied as _mark

    typer.echo(_mark(_cfg(env), job_id))


@app.command("send-pending")
def send_pending_cmd(env: Path | None = typer.Option(None)):
    """Send recruiter emails whose delay has elapsed and are inside the send window (policy caps apply)."""
    from worker.handoff_done import send_pending

    typer.echo(f"sent {send_pending(_cfg(env))}")


if __name__ == "__main__":
    app()
