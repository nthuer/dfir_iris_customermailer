# iris_customer_case_mailer_module

DFIR-IRIS **processor module** (`customer_case_mailer`) for IRIS **>= 2.4.27**.

Analysts send a customer-ready **investigation report by email**
directly from a case – without leaving IRIS. Every send attempt
(success or failure) is automatically documented as a **note in the
`Communication` directory**, including the actually sent report as a
file.

---

## Contents

- [Features](#features)
- [Architecture & data flow](#architecture--data-flow)
- [Project structure](#project-structure)
- [Installation](#installation)
- [Customer extension (`contact_emails`)](#customer-extension-contact_emails)
- [Module configuration](#module-configuration)
- [Mail templates](#mail-templates)
- [Usage (dialog & hook)](#usage-dialog--hook)
- [Recipient logic](#recipient-logic)
- [Notes / documentation](#notes--documentation)
- [Error behaviour](#error-behaviour)
- [Security](#security)
- [Tests](#tests)
- [Assumptions & limitations](#assumptions--limitations)
- [License](#license)

---

## Features

- Manual case hook **"Send customer report"** (processor module,
  `on_manual_trigger_case`).
- Send dialog with:
  - preview of the final `To` recipients (not editable),
  - visible, non-editable `CC`/`BCC` from the module configuration,
  - prefilled, **editable subject** (Jinja2 template),
  - selection of **investigation report template**, **report format**
    (`docx`/`html`) and **HTML mail template**,
  - mandatory **preview** of mail body and report,
  - optional **test send**.
- The report is rendered via the **existing IRIS investigation report
  templates** and sent as a mail attachment.
- **Test mode**: mail goes exclusively to configured test recipients,
  never to production addresses.
- After **every** send attempt: exactly one note under `Communication`,
  with the report attached as a real file (IRIS datastore, linked in
  the note).

## Architecture & data flow

Clearly separated components; **all IRIS internals are encapsulated in
the `iris_adapter` only** – everything else is testable without a
running IRIS.

```
Hook layer (IrisCustomerCaseMailerInterface)        UI layer (ui/blueprint.py + dialog.html)
        │  manual hook                                      │  /customer_case_mailer/dialog
        └───────────────┬───────────────────────────────────┘
                        ▼
              CustomerCaseMailer (mailer.py, orchestrator)
                        │
   1. config_service    │  loads/validates the module configuration
   2. iris_adapter      │  loads case + customer + contact_emails
   3. recipient_service │  parse CSV, trim, validate, dedupe,
                        │  merge CC/BCC, apply test mode
   4. template_service  │  render subject + HTML mail body (Jinja2, sandboxed)
   5. report_service    │  render investigation report (docx/html)
   6. smtp_service      │  build MIME mail, TLS/auth, send
   7. notes_service     │  ensure 'Communication' directory,
                        │  create note, attach report as file
   8. audit_service     │  logging with secret masking (everywhere)
                        ▼
                   SendResult → UI / hook task feedback
```

If a step fails, the send is aborted, the error is reported to the
analyst **and** a `FAILED` note is created (best effort) containing all
data known up to that point.

## Project structure

```
iris_customer_case_mailer_module/
├── __init__.py                        # exports the interface class for IRIS
├── IrisCustomerCaseMailerInterface.py # hook layer (processor module)
├── customer_case_mailer_conf.py       # module metadata + configuration definition
└── customer_case_mailer/              # domain logic (testable without IRIS)
    ├── models.py                      # dataclasses (config, CaseContext, ...)
    ├── errors.py                      # typed error classes
    ├── config_service.py              # config layer
    ├── recipient_service.py           # recipient service
    ├── template_service.py            # template service (Jinja2 sandboxed)
    ├── report_service.py              # report service (investigation reports)
    ├── smtp_service.py                # SMTP service (MIME, TLS, auth)
    ├── notes_service.py               # notes service (Communication, attachment)
    ├── audit_service.py               # error/audit service (secret masking)
    ├── iris_adapter.py                # the ONLY place touching IRIS internals
    └── ui/
        ├── blueprint.py               # send dialog (Flask blueprint)
        └── templates/dialog.html      # dialog frontend (vanilla JS)
examples/mail_templates/standard_customer_mail.html
tests/                                 # 50 unit/flow tests (run without IRIS)
```

## Installation

1. **Install the package into the IRIS environment** (inside the
   `iriswebapp_app` and `iriswebapp_worker` containers):

   ```bash
   pip install /path/to/iris_customer_case_mailer_module
   # or from a git repo:
   pip install git+https://…/iris_customer_case_mailer_module.git
   ```

2. **Provide the mail templates** (the path must be reachable for the
   webapp *and* the worker, e.g. via a volume):

   ```bash
   mkdir -p /opt/iris/customer_case_mailer/mail_templates
   cp examples/mail_templates/standard_customer_mail.html \
      /opt/iris/customer_case_mailer/mail_templates/
   ```

3. **Register the module in IRIS**: *Advanced → Modules → Add module* →
   enter the module name `iris_customer_case_mailer_module`.

4. **Configure** (see below) and **enable** the module.

5. **Restart the IRIS services** (webapp + worker) so that hook and
   dialog blueprint get registered:

   ```bash
   docker compose restart app worker
   ```

## Customer extension (`contact_emails`)

The module reads the `To` recipients from a **customer custom
attribute** with the fixed name `contact_emails` (CSV string):

```
customer1@example.org,customer2@example.org
```

Setup in IRIS: *Advanced → Custom Attributes → Client* → add the
attribute `contact_emails` (type text/input), e.g.:

```json
{
    "Contact": {
        "contact_emails": {
            "type": "input_string",
            "mandatory": false,
            "value": ""
        }
    }
}
```

Then maintain the recipient addresses on every customer. The attribute
name is configurable via `customer_email_attribute`; the default is
`contact_emails`.

## Module configuration

| Parameter | Type | Mandatory | Default | Description |
|---|---|---|---|---|
| `smtp_host` | string | yes | – | SMTP server |
| `smtp_port` | int | yes | 587 | 587 = STARTTLS, 465 = implicit TLS |
| `smtp_use_tls` | bool | yes | true | use STARTTLS |
| `smtp_username` | string | no | – | empty ⇒ send without auth |
| `smtp_password` | secret | no | – | never logged/written to notes |
| `smtp_from_address` | string | yes | – | sender address |
| `smtp_from_name` | string | no | – | sender name |
| `smtp_timeout_seconds` | int | no | 30 | connect/send timeout |
| `default_cc` | CSV | no | – | fixed CC, visible in the dialog |
| `default_bcc` | CSV | no | – | fixed BCC, visible in the dialog |
| `test_mode_enabled` | bool | yes | false | see test mode |
| `test_mode_recipients` | CSV | no* | – | *mandatory when test mode is on |
| `customer_email_attribute` | string | yes | `contact_emails` | source of the To addresses |
| `notes_directory_name` | string | yes | `Communication` | notes directory |
| `default_subject_template` | string | yes | `Investigation Report – {{ case.name }} ({{ case.soc_id }})` | subject template |
| `allowed_report_formats` | CSV | yes | `docx,html` | only `docx`/`html` |
| `allowed_report_templates` | CSV | no | – | names/ids; empty = all |
| `allowed_mail_templates` | CSV | no | – | file names; empty = all |
| `mail_templates_dir` | string | yes | `/opt/iris/customer_case_mailer/mail_templates` | HTML mail templates |
| `default_mail_template` | string | no | `standard_customer_mail.html` | preselection/hook send |
| `default_report_template` | string | no | – | preselection/hook send |
| `default_report_format` | string | no | `docx` | preselection/hook send |
| `manual_hook_sends_with_defaults` | bool | no | false | hook sends directly using defaults |

The configuration is validated strictly on load (`config_service`);
invalid values (e.g. a broken CC address) block sending with an
understandable error message.

## Mail templates

- HTML files (`.html`/`.htm`) in `mail_templates_dir`; multiple
  templates are supported and selectable in the dialog.
- **Same template syntax as the IRIS report templates** (Jinja2:
  `{{ … }}`, `{% … %}`).
- Supported variables (at minimum):
  `case.name`, `case.description`, `case.open_date`,
  `case.for_customer`, `case.soc_id`.
  - `case.for_customer` is mapped to the **name of the case customer**
    (IRIS data model: `Cases.client → Client.name`); template
    compatibility is preserved.
  - Additionally available: `case.customer.name`,
    `case.customer.attributes` (all customer custom attributes).
- Rendering is **sandboxed** with `StrictUndefined` (missing variables
  ⇒ hard error) and **autoescape** (case data cannot inject HTML/JS).
- The subject is rendered from `default_subject_template` and can be
  overridden in the dialog. Multilingualism is handled via the
  templates themselves (no language logic inside the module).

## Usage (dialog & hook)

**Dialog (primary path):** `https://<iris>/customer_case_mailer/dialog?cid=<case_id>`

The dialog shows To/CC/BCC (read-only), the subject (editable), the
template/format selection as well as the mandatory preview of mail body
and report. The **Send** button only becomes active once recipients and
subject are valid **and both previews have been generated**; any change
to the selection invalidates the previews. If a test recipient is
configured (but test mode is not globally enabled), the dialog
additionally offers an explicit **test send**.

**Hook:** In the case under *Actions → Send customer report*.
- Default: the hook reports the dialog link as its task result.
- With `manual_hook_sends_with_defaults=true` the hook sends directly
  using the configured default templates (headless, e.g. for
  standardised closing reports).

**Test mode:** With `test_mode_enabled=true` mail is **never** sent to
production recipients: `To` is replaced by `test_mode_recipients`,
CC/BCC are dropped, the subject is prefixed with `[TEST MODE]`, and the
note documents the originally intended production recipients.

## Recipient logic

1. `To` = customer attribute `contact_emails`, split by `,`.
2. Whitespace trimmed, empty values removed.
3. Every address validated (To, CC and BCC) – **a single invalid
   address blocks the send**.
4. Deduplicated case-insensitively (first spelling wins).
5. `CC`/`BCC` exclusively from the module configuration.
6. Recipients are assembled **server-side and final** – the dialog only
   displays them, frontend input for recipients is ignored.

## Notes / documentation

After **every** send attempt exactly one note is created in the
`Communication` directory (created automatically when missing):

- Title on success: `Customer mail YYYY-MM-DD HH:MM – <Customer>`
- Title on failure: `FAILED – Customer mail YYYY-MM-DD HH:MM – <Customer>`
- Content: timestamp, final To/CC/BCC, final subject, analyst identity,
  rendered mail body, error details if any.
- The actually sent report is stored as a **real file** in the case
  **datastore** and linked inside the note (IRIS notes have no native
  attachment field; the datastore is the IRIS-conformant location for
  files on a case).
- No separate status field – success/failure only via title and content.

## Error behaviour

Cleanly handled cases (each with a UI message **and** a failure note):
no customer on the case · `contact_emails` missing/empty · invalid
address in To/CC/BCC · report/mail template not renderable (**hard
blocker**) · SMTP unreachable · authentication failed · TLS error ·
timeout · note directory/note not creatable · attachment not storable
(documented inside the note, does not abort the documentation).

Special case: if the note fails **after a successful send**, the result
stays "sent", but the analyst is explicitly asked to document manually.

## Security

- SMTP password: `sensitive_string`, **never** logged and **never**
  written into notes (masking filter across all logs/error texts).
- Recipients are assembled server-side; template/format selections from
  the frontend are re-validated against allowlists on the server.
- Jinja2 sandbox + StrictUndefined + autoescape; template names are
  checked against the directory listing (no path traversal).
- The subject is normalised to a single line (no header injection).
- The mail body preview runs in a `sandbox` iframe.
- The dialog endpoints require an authenticated IRIS session; without
  an available auth mechanism the dialog is **not** registered
  (fail closed).

## Tests

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt   # Windows
python -m pytest
```

50 tests, runnable without a running IRIS (IRIS access is encapsulated
in the `iris_adapter` and replaced by an in-memory fake in the tests).
Covered among others: single/multiple CSV recipients, trim/dedupe,
invalid addresses, SMTP with/without auth, TLS on/off, test mode
override, mail template rendering with `case.*`, report rendering
DOCX/HTML, mail/report preview, automatic directory creation, one note
per send, attachment on the note, failure note on failed send, hard
template errors.

## Assumptions & limitations

All IRIS-specific assumptions are marked in the code and located
**only** in `customer_case_mailer/iris_adapter.py` and
`ui/blueprint.py`:

1. **Dialog:** the IRIS hook framework (2.4.x) cannot open
   parameterised dialogs. Because modules run inside the IRIS webapp
   process, the module registers its own Flask blueprint at load time
   (`/customer_case_mailer/…`). This is a deliberate, documented
   extension point outside the official module API; if registration
   fails, the hook send with defaults remains usable.
2. **Reporter signatures:** `IrisMakeDocReport`/`IrisMakeMdReport`
   vary between minor versions; the adapter uses feature detection.
   On deviations adapt only the adapter.
3. **Note attachment:** realised as a datastore file + link inside the
   note (IRIS-conformant, since notes have no native attachments).
4. **HTML report preview** opens in a new tab; the DOCX preview is
   delivered as a download (browsers cannot render DOCX natively).
5. After installation/configuration changes a **restart of the IRIS
   services** may be required (hook/blueprint registration).

## License

This project is licensed under the **Apache License, Version 2.0** –
see the [LICENSE](LICENSE) file for the full license text.

```
Copyright 2026 SOC Engineering

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

In short, this means you are free to use, modify and distribute this
module (including commercially), as long as you retain the license and
copyright notice. The software is provided **without warranty** of any
kind.
