"""Ashby-like application fixture: text fields, yes/no button pairs (aria-pressed), consent radio, file input."""
JS = """
document.querySelectorAll('.ashby-application-form-input-yesno-option').forEach(b => b.addEventListener('click', e => {
  e.preventDefault(); const grp = b.parentElement; grp.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed','false'));
  b.setAttribute('aria-pressed','true'); grp.querySelector('input[type=hidden]').value = b.textContent.trim(); }));
document.getElementById('f').addEventListener('submit', e => {
  e.preventDefault(); const missing = [];
  document.querySelectorAll('[data-required="1"]').forEach(q => {
    const t = q.querySelector('input[type=text],input[type=email],input[type=tel],textarea'); const h = q.querySelector('input[type=hidden]');
    const f = q.querySelector('input[type=file]'); const r = q.querySelectorAll('input[type=radio]');
    let ok=false; if (t) ok = t.value.trim()!==''; else if (h) ok = h.value!==''; else if (f) ok = f.files.length>0; else if (r.length) ok=[...r].some(x=>x.checked);
    if (!ok) missing.push(q.dataset.label); });
  if (missing.length) { document.getElementById('err').textContent = 'Your form needs corrections: ' + missing.join('; '); return; }
  document.body.innerHTML = '<h2>Your application was successfully submitted</h2>'; });
"""


def text(label, id_, req=True, type_="text"):
    return f'<div class="q" data-required="{int(req)}" data-label="{label}"><label for="{id_}">{label}{" *" if req else ""}</label><input id="{id_}" type="{type_}" aria-label="{label}"></div>'


def yesno(label, id_, req=True):
    return (f'<div class="q" data-required="{int(req)}" data-label="{label}"><span class="ashby-application-form-field-title" id="{id_}-lbl">{label}{" *" if req else ""}</span>'
            f'<div role="group" aria-labelledby="{id_}-lbl"><button type="button" class="ashby-application-form-input-yesno-option" aria-pressed="false">Yes</button>'
            f'<button type="button" class="ashby-application-form-input-yesno-option" aria-pressed="false">No</button><input type="hidden" name="{id_}"></div></div>')


def consent(label, id_):
    return (f'<div class="q" data-required="1" data-label="{label}"><fieldset><legend>{label} *</legend>'
            f'<label><input type="radio" name="{id_}" value="agree"> I agree</label></fieldset></div>')


def build(extra=""):
    body = "".join([
        text("Name", "_systemfield_name"), text("Email", "_systemfield_email", type_="email"),
        '<div class="q" data-required="1" data-label="Resume"><label for="_systemfield_resume">Resume *</label><input id="_systemfield_resume" type="file" aria-label="Resume"></div>',
        text("Phone", "phone", req=False), text("LinkedIn Profile", "linkedin", req=False),
        text("Current or most recent employer", "employer"), text("Current or most recent job title", "title"),
        yesno("Are you legally authorized to work in the United States?", "auth"),
        yesno("Will you now or in the future require sponsorship for employment visa status?", "sponsor"),
        yesno("Have you ever been terminated or fired from a job?", "fired"),
        yesno("May we contact your current employer?", "contact_current"),
        consent("I agree to the privacy policy and consent to the processing of my data", "consent"),
        extra, '<div id="err"></div><button type="submit">Submit Application</button>'])
    return f"<!doctype html><html><body><form id='f'>{body}</form><script>{JS}</script></body></html>"
