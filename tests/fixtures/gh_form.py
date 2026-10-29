"""Builds Greenhouse-like fixture HTML (job-boards.greenhouse.io structure) with optional extra questions."""
from __future__ import annotations

SELECT_JS = """
document.querySelectorAll('.select__control').forEach(ctrl => {
  const input = ctrl.querySelector('input[role=combobox]');
  const opts = JSON.parse(ctrl.dataset.options);
  const hidden = ctrl.querySelector('input[type=hidden]');
  const val = ctrl.querySelector('.select__single-value');
  let menu = null;
  function open(filter) {
    close();
    menu = document.createElement('div'); menu.setAttribute('role','listbox'); menu.className='select__menu';
    opts.filter(o => !filter || o.toLowerCase().includes(filter.toLowerCase())).forEach(o => {
      const d = document.createElement('div'); d.setAttribute('role','option'); d.className='select__option'; d.textContent = o;
      d.addEventListener('mousedown', e => { e.preventDefault(); choose(o); });
      menu.appendChild(d);
    });
    ctrl.appendChild(menu);
  }
  function close(){ if (menu) { menu.remove(); menu = null; } }
  function choose(o){ val.textContent = o; hidden.value = o; input.value=''; close(); }
  input.addEventListener('focus', () => open(''));
  input.addEventListener('input', () => open(input.value));
  input.addEventListener('keydown', e => { if (e.key === 'Enter' && menu && menu.firstChild) { choose(menu.firstChild.textContent); e.preventDefault(); } if (e.key==='Escape') close(); });
  input.addEventListener('blur', () => setTimeout(close, 50));
});
document.getElementById('app').addEventListener('submit', e => {
  e.preventDefault();
  const missing = [];
  document.querySelectorAll('[data-required="1"]').forEach(q => {
    const t = q.querySelector('input[type=text],input[type=email],input[type=tel],textarea');
    const h = q.querySelector('input[type=hidden]');
    const f = q.querySelector('input[type=file]');
    const cbs = q.querySelectorAll('input[type=checkbox]');
    let ok = false;
    if (t) ok = t.value.trim().length > 0;
    else if (h) ok = h.value.length > 0;
    else if (f) ok = f.files.length > 0;
    else if (cbs.length) ok = [...cbs].some(c => c.checked);
    if (!ok) missing.push(q.dataset.label);
  });
  if (missing.length) { document.getElementById('err').textContent = 'Please complete: ' + missing.join('; '); return; }
  document.body.innerHTML = '<h1>Thank you for applying.</h1><p>Your application has been received.</p>';
});
"""


def text_q(label, id_, required=True, type_="text"):
    return (f'<div class="question" data-required="{int(required)}" data-label="{label}">'
            f'<label for="{id_}">{label}{"*" if required else ""}</label><input id="{id_}" name="{id_}" type="{type_}" aria-label="{label}"></div>')


def select_q(label, id_, options, required=True):
    import html
    import json
    opts = html.escape(json.dumps(options), quote=True)
    return (f'<div class="question" data-required="{int(required)}" data-label="{label}">'
            f'<label for="{id_}">{label}{"*" if required else ""}</label>'
            f'<div class="select__control" data-options="{opts}">'
            f'<div class="select__single-value"></div><input id="{id_}" role="combobox" aria-label="{label}" autocomplete="off">'
            f'<input type="hidden" name="{id_}"></div></div>')


def checkbox_q(label, id_, options, required=True):
    boxes = "".join(f'<label><input type="checkbox" name="{id_}" value="{o}">{o}</label>' for o in options)
    return (f'<fieldset class="question" data-required="{int(required)}" data-label="{label}">'
            f'<legend>{label}{"*" if required else ""}</legend>{boxes}</fieldset>')


def build(extra_questions: str = "", title: str = "AI Engineer") -> str:
    body = "".join([
        text_q("First Name", "first_name"), text_q("Last Name", "last_name"), text_q("Email", "email", type_="email"),
        select_q("Country", "country", ["United States +1", "Canada +1", "India +91"]),
        text_q("Phone", "phone", type_="tel"),
        select_q("Location (City)", "candidate-location", ["St. Louis, Missouri, United States", "Lake St. Louis, Missouri, United States"]),
        '<div class="question" data-required="1" data-label="Resume/CV"><label for="resume">Resume/CV*</label><input id="resume" type="file" aria-label="Resume/CV"></div>',
        '<div class="question" data-required="0" data-label="Cover Letter"><label for="cover">Cover Letter</label><input id="cover" type="file" aria-label="Cover Letter"></div>',
        text_q("LinkedIn Profile", "question_1", required=False), text_q("Website", "question_2", required=False),
        select_q("Are you open to working in person in our San Francisco or New York office 3 times a week?", "question_3", ["Yes", "No"], required=False),
        select_q("Are you currently bound by any agreements with a current or former employer that may restrict your ability to work for us?", "question_4", ["Yes", "No"]),
        select_q("Are you legally authorized to work in the country where the job is located?", "question_5", ["Yes", "No"]),
        select_q("Will you now or in the future require company sponsorship to retain or extend your work authorization?", "question_6", ["Yes", "No"]),
        text_q("Who is your current or most recent employer?", "question_7"), text_q("What is your current or most recent job title?", "question_8"),
        select_q("Gender", "gender", ["Male", "Female", "Decline To Self Identify"], required=False),
        select_q("Are you Hispanic/Latino?", "hispanic", ["Yes", "No", "Decline To Self Identify"], required=False),
        select_q("Veteran Status", "veteran", ["I am not a protected veteran", "I identify as one or more of the classifications of a protected veteran", "I don't wish to answer"], required=False),
        extra_questions,
        '<div id="err"></div><button type="submit">Submit application</button>',
    ])
    return f"<!doctype html><html><head><title>Job Application for {title}</title></head><body><h1>Apply for this job</h1><form id='app'>{body}</form><script>{SELECT_JS}</script></body></html>"
