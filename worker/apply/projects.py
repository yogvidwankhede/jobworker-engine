"""Choose project examples exclusively from the verified candidate ledger."""
import re


def select_project(posting, projects):
    text = posting.title + ' ' + posting.jd_text
    signals = [(r'eval|reliability|statistic|measurement', 'rubricon'),
               (r'medical|health|clinical|retrieval|\brag\b|embedding', 'healthmate'),
               (r'agent|orchestrat|workflow', 'ventureos'),
               (r'developer experience|coding agent|design system|frontend', 'vishwakarma')]
    verified = [p for p in projects if p.get('verified') is True and p.get('facts')]
    for pattern, identifier in signals:
        if re.search(pattern, text, re.I):
            project = next((p for p in verified if p.get('id') == identifier), None)
            if project:
                return project
    return next((p for p in verified if p.get('id') == 'ventureos'), None)


def describe(project):
    facts = [f['claim'] for f in project['facts'] if f.get('verified', True) and f.get('claim')][:2]
    url = project.get('url_repo') or project.get('url') or project.get('url_live') or ''
    return f"A relevant project is {project['name']}. The project includes " + '; '.join(facts) + f'. {url}'
