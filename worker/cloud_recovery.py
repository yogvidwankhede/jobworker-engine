"""Retry one unstarted production run after a confirmed hosted-runner acquisition failure."""
import json
import os
from pathlib import Path
import re
import subprocess


def candidate(run):
    return (run.get('name') == 'JobWorker Cloud Run' and run.get('head_branch') == 'main'
            and run.get('status') == 'completed' and run.get('conclusion') == 'failure'
            and run.get('run_attempt') == 1 and type(run.get('id')) is int and run['id'] > 0
            and isinstance(run.get('head_sha'), str) and bool(run['head_sha']))


def recover(repository, run, get, rerun):
    if not candidate(run):
        return 'No retry: only the first failed production attempt is eligible.'
    root = f'repos/{repository}'
    if get(root + '/commits/main').get('sha') != run.get('head_sha'):
        return 'No retry: a newer deployment exists.'
    response = get(root + f"/actions/runs/{run['id']}/attempts/1/jobs?per_page=100")
    jobs = response.get('jobs', [])
    if not jobs or response.get('total_count') != len(jobs):
        return 'No retry: the full job history is unavailable.'
    for job in jobs:
        if job.get('steps') != [] or job.get('status') != 'completed' or job.get('conclusion') != 'cancelled':
            return 'No retry: a job started or failed for another reason.'
        url = job.get('check_run_url', '')
        prefix = 'https://api.github.com/' + root + '/check-runs/'
        if not url.startswith(prefix) or not re.fullmatch(r'\d+', url[len(prefix):]):
            return 'No retry: runner failure evidence is unavailable.'
        annotations = get(url.removeprefix('https://api.github.com/') + '/annotations?per_page=100')
        if not any('job was not acquired by runner of type hosted' in str(item.get('message', '')).lower()
                   for item in annotations):
            return 'No retry: no confirmed runner acquisition failure.'
    # A manual rerun or another deployment may have happened while evidence was read.
    current = get(root + f"/actions/runs/{run['id']}")
    if not candidate(current) or current.get('head_sha') != run.get('head_sha'):
        return 'No retry: this run has already changed.'
    if get(root + '/commits/main').get('sha') != run.get('head_sha'):
        return 'No retry: a newer deployment exists.'
    rerun(root + f"/actions/runs/{run['id']}/rerun-failed-jobs")
    return f"Requested one cloud retry for run {run['id']}; no worker steps previously ran."


def github_json(path):
    result = subprocess.run(['gh', 'api', path], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(result.stdout)


def github_rerun(path):
    subprocess.run(['gh', 'api', '--method', 'POST', path], capture_output=True, text=True, check=True, timeout=30)


if __name__ == '__main__':
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    print(recover(os.environ['GITHUB_REPOSITORY'], event.get('workflow_run', {}), github_json, github_rerun))
