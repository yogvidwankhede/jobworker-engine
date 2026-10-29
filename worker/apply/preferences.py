"""Reusable application answers derived from the candidate's explicitly saved choices."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from worker.apply.answers import Answer, GUARD, question_clause


def advertised_us_locations(posting) -> list[str]:
    if not posting or not posting.location:
        return []
    cities = ('San Francisco', 'New York', 'Seattle', 'Los Angeles', 'Boston', 'Austin', 'Chicago', 'Denver', 'Washington', 'St. Louis')
    found = [(match.start(), city) for city in cities
             if (match := re.search(r'\b' + re.escape(city) + r'\b', posting.location, re.I))]
    return [city for _, city in sorted(found)]


class OfficeHubAnswer(Answer):
    options_required = True

    def choose(self, options):
        hubs = [o for o in options if not re.match(r'\s*(no\b|select\b|choose\b)', o, re.I)]
        # These inspected hubs are all well beyond 50 miles from the saved
        # St. Louis address. A new/unknown hub must be evaluated separately.
        if not hubs or not all(re.search(r'\b(San Francisco|New York|Seattle|London)\b', o, re.I) for o in hubs):
            return None
        return super().choose(options)


class VisaChoiceAnswer(Answer):
    options_required = True

    def choose(self, options):
        yes = next((o for o in options if o.strip().lower() == 'yes'), None)
        if yes:
            return yes
        if self.pattern == 'future_sponsorship':
            return next((o for o in options if o.strip().upper() == 'H1B'), None)
        return next((o for o in options if re.fullmatch(
            r'my current work authorisation requires sponsorship or renewal now or in the future\.?', o.strip(), re.I)), None)


class CurrentLocationAnswer(Answer):
    options_required = True
    search_options = True

    def choose(self, options):
        normal = super().choose(options)
        if normal:
            return normal
        city = next((o for o in options if re.match(r'^St\.?\s*Louis,?\s*(MO|Missouri)\b', o.strip(), re.I)), None)
        if city:
            return city
        missouri = next((o for o in options if re.fullmatch(r'US\s*-\s*Missouri', o.strip(), re.I)), None)
        return missouri or next((o for o in options if o.strip().lower() == 'us - other'), None)


class IdentityChoiceAnswer(Answer):
    """Disambiguate a generic identity label using its actual offered categories."""
    def __init__(self, orientation, transgender):
        super().__init__('select', [], '')
        self.orientation, self.transgender = orientation, transgender

    def choose(self, options):
        choices = {o.strip().lower(): o for o in options}
        if {'cisgender', 'transgender'} <= choices.keys():
            saved = self.transgender.answer if self.transgender else None
            if saved is False or saved == ['No']:
                return choices['cisgender']
            if saved is True or saved == ['Yes']:
                return choices['transgender']
            return None
        return self.orientation.choose(options) if self.orientation else None


class CareersSourceAnswer(Answer):
    def choose(self, options):
        # Answer.choose checks all exact matches before any partial matches.
        # Keep "Other" as a fallback, not ahead of a labelled company website.
        direct = [o for o in options if o.strip().lower() != 'other']
        return super().choose(direct) or next((o for o in options if o.strip().lower() == 'other'), None)


def careers_source(label, posting):
    """Report the actual employer board used by sourcing, not a guessed referral."""
    if not posting or not any(urlsplit(url).hostname in {
            'job-boards.greenhouse.io', 'boards.greenhouse.io', 'job-boards.eu.greenhouse.io',
            'boards.eu.greenhouse.io', 'jobs.ashbyhq.com', 'jobs.lever.co', 'jobs.eu.lever.co'}
            for url in (posting.url, posting.apply_url)):
        return None
    if re.search(r'how did you (hear|find)|where have you learned about', label, re.I):
        return CareersSourceAnswer('select', [f'{posting.company} Careers Site', f'{posting.company} blog or website',
                                             'Company website', 'Company careers page', 'Careers page', 'Careers site',
                                             'Career site', 'Website'], '')
    if re.search(r'please type in your answer if you selected.*current.*employee.*or.*other', label, re.I):
        return Answer('text', "I found this role on the employer's careers website.", '')
    return None


def needs_interview_policy_context(label):
    return bool(re.fullmatch(r'AI Policy for Interviewers\s*\*?', label.strip(), re.I))


def interview_policy_answer(label, context, prefs):
    if not needs_interview_policy_context(label):
        return None
    # Read the actual policy. The candidate agreed to complete assessments
    # personally; that does not authorize unrelated recording or data policies.
    if not re.search(r'do not use any AI tools during any part of the interview process', context, re.I):
        return None
    if not re.search(r'if you have read and agree', context, re.I):
        return None
    saved = prefs.get('willing_to_complete_assessments')
    return Answer('yesno', saved, '') if isinstance(saved, bool) else None


def text_answer(label: str, prefs: dict, posting=None, contact=None) -> Answer | None:
    label = re.sub(r'\s*\*\s*$', '', label).strip()
    if re.search(r'are you currently (located|based|living) in (the )?(US|U\.S\.|United States)\??$', label.strip(), re.I):
        country = (contact or {}).get('country')
        return Answer('yesno', country == 'United States', '') if country else None
    # A list of example authorization categories is not a demand for citizenship.
    # Keep guards on the actual question, including "without sponsorship".
    clause = re.sub(r'\(\s*(e\.g\.|for example)[^)]*\)', '', question_clause(label), flags=re.I)
    if prefs.get('work_authorization_status') == 'F-1 post-completion OPT' and not GUARD.search(clause):
        if re.search(r'require sponsorship', label, re.I) and re.search(r'now.*future|future.*require', label, re.I):
            return VisaChoiceAnswer('yesno', True, 'future_sponsorship')
        if re.search(r'now.*future.*require.*sponsor an immigration case', label, re.I):
            return VisaChoiceAnswer('yesno', True, 'future_sponsorship')
        if re.search(r'now.*future.*require.*commence.*sponsor.*immigration case', label, re.I):
            return VisaChoiceAnswer('yesno', True, 'future_sponsorship')
        if re.search(r'authori[sz]ed to work in the country in which this role is located', label, re.I) and advertised_us_locations(posting):
            return VisaChoiceAnswer('yesno', True, 'current_authorization')
        if re.search(r'currently legally authori[sz]ed to work in the country in which this job is based', clause, re.I) and posting and (
                advertised_us_locations(posting) or re.search(r'\b(US|USA|United States)\b', posting.location, re.I)):
            return VisaChoiceAnswer('yesno', True, 'current_authorization')
    if re.fullmatch(r'current location\s*\*?', label.strip(), re.I):
        contact = contact or {}
        if contact.get('city') == 'St. Louis' and contact.get('state') in {'Missouri', 'MO'} and contact.get('country') == 'United States':
            return CurrentLocationAnswer('text', contact.get('location', 'St. Louis, Missouri, United States'), '')
    if re.search(r'located within 50 miles of one of the following office hubs', label, re.I):
        city = (contact or {}).get('city', '')
        if city == 'St. Louis' and prefs.get('office_locations') == 'any_us_role':
            return OfficeHubAnswer('select', ['No, but willing to relocate', 'No, open to relocating'], '')
    if re.search(r'are you (open|willing|able|comfortable).*(work|working|coming|commuting|reporting).*(in[- ]office|office|on[- ]?site|in[- ]person)|'
                 r'are you able to commit.*working.*offices?|are you aligned.*work from office expectation|'
                 r'office.*are you able to meet this requirement', label, re.I):
        styles = prefs.get('accepted_work_styles')
        if isinstance(styles, list):
            return Answer('yesno', bool({'onsite', 'hybrid'} & set(styles)), '')
    if re.search(r'sponsor.*(which one|which visa|visa exp|expir)', label, re.I):
        if prefs.get('work_authorization_status') == 'F-1 post-completion OPT' and prefs.get('opt_end_date'):
            return Answer('text', f"I am on F-1 post-completion OPT and will require H-1B sponsorship in the future. My current employment authorization (EAD) expires on {prefs['opt_end_date']}.", '')
    if re.search(r"how(?: did|'d) you get into programming", label, re.I):
        if prefs.get('programming_interest_answer'):
            return Answer('text', prefs['programming_interest_answer'], '')
    if re.search(r'what AI tools.*using.*how.*using', label, re.I):
        if prefs.get('current_ai_tools_answer'):
            return Answer('text', prefs['current_ai_tools_answer'], '')
    if re.search(r"how (soon|quickly).*relocat|relocation (timeline|timing)|when.*relocat", label, re.I):
        days = prefs.get('relocation_notice_days')
        if days:
            return Answer('text', 'Within two weeks of accepting an offer.' if days == 14 else f'Within {days} days of accepting an offer.', '')
    if re.search(r"(percent|percentage|how much|amount|frequency).*travel|travel.*(percent|percentage)", label, re.I):
        travel = prefs.get('business_travel')
        if travel is not None:
            return Answer('select', ['100%', 'Up to 100%', 'Any amount required for the role', 'As required'] if travel == 'any' else [str(travel)], '')
    if re.search(r"salary history|previous salary|current (salary|compensation)|past (salary|compensation)", label, re.I):
        if prefs.get('salary_history_policy') == 'decline_where_permitted':
            return Answer('select', ['I prefer not to disclose previous compensation.', 'Prefer not to disclose', 'Decline to disclose', 'Prefer not to answer'], '')
    if re.search(r"reason.*(leav|job search)|why.*(leav|looking for a new)", label, re.I):
        reason = prefs.get('job_search_reason')
        if reason:
            return Answer('text', reason, '')
    if re.search(r"work hours|working hours|hours.*available", label, re.I):
        if prefs.get('work_hours') == 'flexible_including_occasional_evenings':
            return Answer('text', 'Flexible hours, including occasional evenings.', '')
    if re.search(r"type of work.*interested|which.*work.*most interested", label, re.I):
        if prefs.get('work_interest_policy') == 'match_advertised_target_role' and posting:
            from worker.scoring.rules import title_family
            return Answer('select', [title_family(posting)[1], posting.title], '')
    if re.search(r"(work|working) (arrangement|style|model)|onsite.*hybrid.*remote|remote.*hybrid.*onsite", label, re.I):
        if set(prefs.get('accepted_work_styles', [])) == {'onsite', 'hybrid', 'remote'}:
            return Answer('select', ['No preference', 'Any', 'All of the above', 'Onsite, hybrid, or remote', 'Onsite', 'Hybrid', 'Remote'], '', avoid=r'\b(only|cannot|not willing)\b')
    if re.search(r'which (office|location).*(work|applying)|preferred office.*location|location.*willing.*work|office.*preference|'
                 r'where.*(plan|intend).*work.*payroll tax|locations?.*interested in relocating', label, re.I):
        if prefs.get('office_locations') == 'any_us_role' and posting and posting.location:
            locations = advertised_us_locations(posting)
            return Answer('select', locations, '') if locations else None
    if not re.search(r'sponsor|authori[sz]ed to work|eligible to work|expir|end date|start date|valid from|unrestricted|permanent', label, re.I) and re.search(r"(current|type of).*work authori[sz]ation|work authori[sz]ation status|visa (type|status)|immigration status", label, re.I):
        if prefs.get('work_authorization_status') == 'F-1 post-completion OPT':
            # Current OPT is not tied to a particular employer. Future sponsorship
            # remains a separate Yes answer; never claim permanent authorization.
            return Answer('select', ['F-1 post-completion OPT', 'F-1 OPT', 'F1 OPT', 'OPT', 'F-1', 'Can work for any employer'], '', avoid=r'\b(STEM|permanent|unrestricted)\b')
    if re.search(r"(ead|opt|work authori[sz]ation).*(expir|end date|valid until)|expir.*(ead|opt|work authori[sz]ation)", label, re.I):
        if prefs.get('opt_end_date'):
            return Answer('text', prefs['opt_end_date'], '')
    if re.search(r"(ead|opt).*(valid from|start date)|valid from.*(ead|opt)", label, re.I):
        if prefs.get('opt_start_date'):
            return Answer('text', prefs['opt_start_date'], '')
    return None


def professional_example(label: str, experience: list[dict]) -> Answer | None:
    if re.search(r'fail|mistake|conflict|disagree|supervis|manag|lead a team', label, re.I):
        return None
    if not re.search(r'example.*professional experience.*(cultural|company|our).*values', label, re.I):
        return None
    role = next((w for w in experience if w.get('id') == 'euno' and w.get('verified') is True), None)
    if not role:
        return None
    facts = {f.get('id'): f['claim'] for f in role.get('facts', [])
             if f.get('verified') is True and f.get('claim') and not f.get('cut_from_final')}
    if 'euno.pipeline' not in facts or 'euno.docs' not in facts:
        return None
    text = (f"An example from my {role['title']} work at {role['employer']} is contributing to a "
            f"{facts['euno.pipeline']}. I also {facts['euno.docs']}. "
            "These efforts reflect my focus on practical AI products, reliability, and clear handoffs.")
    return Answer('text', text, '')


def requires_reference_details(label: str) -> bool:
    if re.search(r'(background|reference) checks?|consent|authori[sz]|may we contact', label, re.I):
        return False
    return bool(re.search(r'\breferee\b|professional references?|reference\s*(\d|name|e-?mail|phone|contact|relationship)|'
                          r'references?.*(provide|list|contact details)|provide.*references?', label, re.I))


def project_answer(label: str, posting, projects: list[dict]) -> Answer | None:
    if not posting or re.search(r'fail|mistake|conflict|challenge|disagree|supervis|manag|lead a team', label, re.I):
        return None
    if re.search(r'\blink\b|\bURL\b', label, re.I):
        return None
    if not re.search(r'proudest (project|achievement)|project.*(proud|most relevant)|something.*worked on.*proud|'
                     r'describe (a|your).*(project|achievement)|example.*(project|company values)', label, re.I):
        return None
    from worker.apply.projects import select_project, describe
    project = select_project(posting, projects)
    return Answer('text', describe(project), '') if project else None
