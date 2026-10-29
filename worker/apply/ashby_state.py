"""Observe Ashby's normal browser requests without storing applicant data or tokens."""
from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, urlsplit


class FormRequests:
    def __init__(self, page):
        self.pending = set()
        self.changed_at = time.monotonic()
        self.events = []
        self.result = None
        self.save_failed = False
        self.upload_urls = set()
        self.upload_candidates = set()
        page.on('request', self.started)
        page.on('requestfinished', self.finished)
        page.on('requestfailed', self.failed)

    def operation(self, request):
        url = urlsplit(request.url)
        if request.method in {'POST', 'PUT'} and request.url in self.upload_urls:
            return 'file_upload'
        if request.method != 'POST' or url.path != '/api/non-user-graphql':
            return None
        name = parse_qs(url.query).get('op', [''])[0]
        if not name:
            try:
                name = (request.post_data_json or {}).get('operationName', '')
            except Exception:
                name = ''
        return name if re.fullmatch(r'[A-Za-z0-9_]{1,100}', name) else 'graphql'

    def started(self, request):
        # A file request can start before the upload-handle response callback
        # finishes. Retain it briefly so registration below cannot miss it.
        if request.method == 'PUT' or (request.method == 'POST' and
                request.headers.get('content-type', '').startswith('multipart/form-data')):
            self.upload_candidates.add(request)
        operation = self.operation(request)
        if operation:
            self.pending.add(request)
            self.changed_at = time.monotonic()
            self.events.append({'operation': operation, 'state': 'started'})

    def finished(self, request):
        self.upload_candidates.discard(request)
        operation = self.operation(request)
        if not operation:
            return
        self.pending.discard(request)
        self.changed_at = time.monotonic()
        response = request.response()
        event = {'operation': operation, 'state': 'finished', 'http': response.status}
        payload = {}
        if operation != 'file_upload':
            try:
                payload = response.json()
            except Exception:
                pass
        if not isinstance(payload, dict):
            payload = {}
        if operation == 'ApiCreateFileUploadHandle':
            handle = (payload.get('data') or {}).get('fileUploadHandle') or {}
            upload_url = handle.get('url')
            if isinstance(upload_url, str):
                # These signed URLs stay in memory; proof contains no URLs,
                # credentials, form values or file contents.
                self.upload_urls.add(upload_url)
                for candidate in self.upload_candidates:
                    if candidate.url == upload_url and candidate not in self.pending:
                        self.pending.add(candidate)
                        self.events.append({'operation': 'file_upload', 'state': 'started'})
        codes = [error['extensions'].get('ashbyErrorType')
                 for error in payload.get('errors', []) if isinstance(error, dict)
                 and isinstance(error.get('extensions'), dict)]
        event['error_codes'] = [code for code in codes if isinstance(code, str) and re.fullmatch(r'[A-Za-z0-9_]{1,120}', code)]
        if operation.startswith(('ApiSetFormValue', 'ApiCreateFileUploadHandle')) or operation == 'file_upload':
            self.save_failed |= response.status >= 400 or bool(payload.get('errors'))
        if operation.startswith('ApiSubmit'):
            data = payload.get('data') or {}
            action = data.get('submitApplicationFormAction') or data.get('submitMultipleFormsAction') or {}
            result = action.get('applicationFormResult') or {}
            typename = result.get('__typename')
            if (action.get('messages') or {}).get('blockMessageForCandidateHtml'):
                self.result = 'blocked'
            elif typename == 'FormSubmitSuccess':
                self.result = 'applied'
            elif typename == 'FormRender':
                self.result = 'validation'
            elif 'RECAPTCHA_SCORE_BELOW_THRESHOLD' in event['error_codes']:
                self.result = 'blocked'
            event['result'] = self.result
        self.events.append(event)

    def failed(self, request):
        self.upload_candidates.discard(request)
        operation = self.operation(request)
        if operation:
            self.pending.discard(request)
            self.changed_at = time.monotonic()
            response = request.response()
            # Chromium can report ERR_ABORTED for an accepted empty S3 POST
            # response. HTTP 204 has no body; the subsequent file-binding
            # mutation is tracked separately before Submit is allowed.
            if operation == 'file_upload' and response and response.status in {204, 205}:
                self.events.append({'operation': operation, 'state': 'empty_response_accepted', 'http': response.status})
                return
            event = {'operation': operation, 'state': 'network_failed'}
            if response:
                event['http'] = response.status
            self.events.append(event)
            if not operation.startswith('ApiSubmit'):
                self.save_failed = True
