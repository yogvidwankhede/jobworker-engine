import pytest

from worker.cloud_recovery import recover


REPO = 'owner/worker'
ROOT = 'repos/' + REPO
RUN = {'id': 123, 'name': 'JobWorker Cloud Run', 'head_branch': 'main', 'head_sha': 'deployed',
       'status': 'completed', 'conclusion': 'failure', 'run_attempt': 1}
JOB = {'status': 'completed', 'conclusion': 'cancelled', 'steps': [],
       'check_run_url': 'https://api.github.com/' + ROOT + '/check-runs/456'}


def exercise(run=None, job=None, annotations=None, current=None, sha='deployed', count=1):
    requested = []
    responses = {
        ROOT + '/commits/main': {'sha': sha},
        ROOT + '/actions/runs/123/attempts/1/jobs?per_page=100': {'jobs': [job or JOB], 'total_count': count},
        ROOT + '/check-runs/456/annotations?per_page=100': annotations if annotations is not None else [
            {'message': 'The job was not acquired by Runner of type hosted even after multiple attempts'}],
        ROOT + '/actions/runs/123': current or RUN,
    }
    result = recover(REPO, run or RUN, lambda path: responses[path], requested.append)
    return result, requested


def test_confirmed_unstarted_runner_failure_requests_one_retry():
    result, requests = exercise()
    assert requests == [ROOT + '/actions/runs/123/rerun-failed-jobs']
    assert 'no worker steps previously ran' in result


@pytest.mark.parametrize('change', [
    {'run_attempt': 2}, {'head_branch': 'feature'}, {'conclusion': 'success'}, {'status': 'queued'},
    {'name': 'Worker Tests'}, {'id': True}, {'head_sha': None},
])
def test_other_runs_never_retry(change):
    assert exercise(run=dict(RUN, **change))[1] == []


@pytest.mark.parametrize('change', [
    {'steps': [{'name': 'Run the worker', 'conclusion': 'failure'}]},
    {'steps': None}, {'status': 'queued'}, {'conclusion': 'failure'},
    {'check_run_url': 'https://example.com/check-runs/456'},
])
def test_started_or_unverified_jobs_never_retry(change):
    assert exercise(job=dict(JOB, **change))[1] == []


def test_manual_retry_and_new_deployment_prevent_duplicate_or_stale_recovery():
    assert exercise(current=dict(RUN, run_attempt=2, status='queued'))[1] == []
    assert exercise(sha='new-deployment')[1] == []
    assert exercise(annotations=[{'message': 'Application submission failed'}])[1] == []
    assert exercise(count=2)[1] == []


def test_deployment_changes_during_evidence_check_prevent_retry():
    requests = []
    main_reads = iter(['deployed', 'new-deployment'])

    def get(path):
        if path == ROOT + '/commits/main':
            return {'sha': next(main_reads)}
        if '/attempts/1/jobs?' in path:
            return {'jobs': [JOB], 'total_count': 1}
        if '/annotations?' in path:
            return [{'message': 'The job was not acquired by Runner of type hosted'}]
        return RUN

    assert 'newer deployment' in recover(REPO, RUN, get, requests.append)
    assert requests == []


def test_privileged_recovery_checks_out_main_and_has_no_application_secrets():
    from pathlib import Path
    import yaml
    path = Path(__file__).resolve().parents[2] / '.github/workflows/jobworker-recover-runner.yml'
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert workflow['on']['workflow_run'] == {'workflows': ['JobWorker Cloud Run'], 'types': ['completed']}
    checkout, recovery = workflow['jobs']['recover']['steps']
    assert checkout['with'] == {'ref': 'main', 'persist-credentials': 'false'}
    assert recovery['env'] == {'GH_TOKEN': '${{ github.token }}'}
    assert workflow['permissions'] == {'contents': 'read', 'actions': 'write', 'checks': 'read'}
