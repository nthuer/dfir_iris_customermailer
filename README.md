# iris_customer_case_mailer_module

DFIR-IRIS **processor module** (`customer_case_mailer`) for IRIS **>= 2.4.27**
(verified against a live **v2.4.29** installation).

Analysts send a customer-ready **investigation report by email**
directly from a case – without leaving IRIS. Recipients are derived
from the **contacts configured on the customer**: everyone whose
contact role is **CISO** and who has an email address. The analyst
first creates a **preview**, which is stored as a note in the case, and
then sends exactly that content. Every preview and every send attempt
is documented as a **note in the `Communication` directory**, including
the rendered report as a file.

---

## Contents

- [Features](#features)
- [How it works in a case](#how-it-works-in-a-case)
- [Architecture & data flow](#architecture--data-flow)
- [Project structure](#project-structure)
- [Installation](#installation)
- [Customer contacts (CISO)](#customer-contacts-ciso)
- [Module configuration](#module-configuration)
- [Per-case send options](#per-case-send-options)
- [Mail templates](#mail-templates)
- [Recipient logic](#recipient-logic)
- [Notes / documentation](#notes--documentation)
- [Error behaviour](#error-behaviour)
- [Security](#security)
- [Tests](#tests)
- [Assumptions & limitations](#assumptions--limitations)
- [License](#license)

---

## Features

- Three manual case hooks in the case *Processors* menu (⚡ in the case navigation bar):
  **Preview customer report**, **Send customer report**,
  **Test send customer report**.
- **Mandatory preview:** a production send only goes out if a preview
  note with *exactly* the same content exists (recipients, subject,
  mail body, templates, format). Any change in between requires a new
  preview.
- The report is rendered via the **existing IRIS investigation report
  templates** (DOCX or HTML) and sent as a mail attachment.
- HTML mail templates with the **same Jinja2 syntax as the IRIS report
  templates**; a default template ships with the module.
- Template, report format and subject can be chosen **per case** via
  case custom attributes, otherwise the module defaults apply.
- **Test mode / test send:** mail goes exclusively to the configured
  test recipients, never to production addresses.
- Every preview and every send attempt produces exactly one note under
  `Communication`, with the report attached as a real file (IRIS
  datastore, linked in the note) and the analyst's identity.

## How it works in a case

1. **Processors → Preview customer report.** The module resolves the
   recipients, renders subject, mail and report, and stores everything
   as a `PREVIEW – Customer mail …` note. **Nothing is sent.**
2. The analyst reviews the preview note: To/CC/BCC, subject, the
   rendered mail body and the attached report.
3. **Processors → Send customer report.** The module renders again and
   sends only if the result matches a preview note (same *content
   fingerprint*). The outcome is stored as a note:
   `Customer mail …` on success, `FAILED – Customer mail …` otherwise.
4. Optional: **Processors → Test send customer report** sends the same
   mail to the configured `test_mode_recipients` only, with the subject
   prefixed by `[TEST MODE]`. No preview is required for test sends.

> IRIS acknowledges a manual hook in the UI only with *"Queued task"*.
> The actual result – including every error – appears as a note in the
> case's `Communication` directory. Refresh the notes after triggering
> a hook.

## Architecture & data flow

Clearly separated components; **all IRIS internals are encapsulated in
the `iris_adapter` only** – everything else is testable without a
running IRIS.

```
IRIS case  →  Processors menu  →  manual hook (synchronous, analyst's request)
                                   │
              IrisCustomerCaseMailerInterface  (hook layer)
              hook_actions.run_hook            (hook → action)
                                   │
              CustomerCaseMailer (mailer.py, orchestrator)
                                   │
   1. config_service    │  loads/validates the module configuration
   2. iris_adapter      │  loads case + customer + contacts + case send options
   3. recipient_service │  pick contacts with role CISO, validate, dedupe,
                        │  merge CC/BCC, apply test mode
   4. template_service  │  render subject + HTML mail body (Jinja2, sandboxed)
   5. report_service    │  render investigation report (docx/html)
   6. mailer            │  content fingerprint
      ├─ preview        │  → PREVIEW note, nothing sent
      └─ send           │  → preview gate → smtp_service → note
   7. notes_service     │  ensure 'Communication' directory,
                        │  create note, attach report as file
   8. audit_service     │  logging with secret masking (everywhere)
```

If a step fails, nothing is sent and a `FAILED` (or `PREVIEW FAILED`)
note is created containing all data known up to that point.

## Project structure

```
buildnpush2iris.sh                     # build the wheel + install into IRIS
Makefile                               # make test / build / install / clean
setup.py, MANIFEST.in                  # packaging (wheel incl. mail templates)
iris_customer_case_mailer_module/
├── __init__.py                        # declares __iris_module_interface
├── IrisCustomerCaseMailerInterface.py # hook layer (registers the 3 hooks)
├── customer_case_mailer_conf.py       # module metadata + configuration definition
└── customer_case_mailer/              # domain logic (testable without IRIS)
    ├── hook_actions.py                # hook names → preview / send / test send
    ├── mailer.py                      # orchestrator, fingerprint, preview gate
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
    └── mail_templates/                # default HTML mail templates (shipped)
tests/                                 # 90 unit/flow tests (run without IRIS)
```

## Installation

Installation follows the usual DFIR-IRIS module flow: build a wheel and
install it into the IRIS containers, then register the module.

**Requirements**

- a running DFIR-IRIS (>= 2.4.27) deployment,
- on the host: `docker`, `python3` with `setuptools` and `wheel`
  (or `build`) – e.g. `python3 -m pip install setuptools wheel`,
- **at least one Investigation report template in IRIS.** A fresh IRIS
  installation has none. Upload one under *Advanced → Report
  templates* with type *Investigation* (IRIS ships a sample in its
  source: `source/app/templates/docx_reports/iris_report_template.docx`).

### 1. Clone and install

```bash
git clone https://github.com/nthuer/dfir_iris_customermailer
cd dfir_iris_customermailer
./buildnpush2iris.sh -a
```

The script builds the wheel, checks that the mail templates are inside,
copies it to `/iriswebapp/dependencies/` in the containers, installs it
with `pip3 install --force-reinstall` and restarts the containers.

**Always use `-a`** (app *and* worker container). The hooks run
synchronously inside the **app** container, which the IRIS convention
only covers with `-a`; without a flag only the worker is updated.

Non-standard container names can be overridden:

```bash
IRIS_APP_CONTAINER=my_app IRIS_WORKER_CONTAINER=my_worker ./buildnpush2iris.sh -a
```

**On Windows (Git Bash with Docker Desktop):** two additions are needed
– `PYTHON` pointing to a Python with `setuptools`/`wheel` (there is no
`python3` on Windows), and `MSYS_NO_PATHCONV=1`, otherwise Git Bash
rewrites container paths like `/iriswebapp/dependencies` into Windows
paths:

```bash
MSYS_NO_PATHCONV=1 PYTHON=/c/path/to/venv/Scripts/python.exe ./buildnpush2iris.sh -a
```

### 2. Register the module

In the web interface:

1. *Advanced → Modules → Add module*
2. Module name: `iris_customer_case_mailer_module`

The module is **active immediately** after registering. Alternatively,
without the web interface (same IRIS function the button uses):

```bash
docker exec -w /iriswebapp iriswebapp_app python3 -c "
from app import app, db
from app.iris_engine.module_handler.module_handler import register_module
with app.app_context():
    print(register_module('iris_customer_case_mailer_module')[1]); db.session.commit()"
```

### 3. Configure

*Advanced → Modules → IrisCustomerCaseMailer*. Everything is
pre-filled except these three values:

| Parameter | Example |
|---|---|
| `smtp_host` | `smtp.example.org` |
| `smtp_from_address` | `soc@example.org` |
| `default_report_template` | name or id of an Investigation report template |

Configuration changes take effect on the next hook call – no restart
needed. For a safe first run, enable `test_mode_enabled` and set
`test_mode_recipients`: then no mail can reach a customer.

The hooks now appear in every case in the *Processors* menu (⚡). Customers still
need a CISO contact, see [Customer contacts](#customer-contacts-ciso).

### 4. Optional: own mail templates

The module ships with a default HTML mail template. To use your own,
mount a directory into **both** containers and point
`mail_templates_dir` at it.

In the `docker-compose.yml` of your IRIS installation, the `app` and
`worker` services are defined via `extends` and have no `volumes:` key
yet – add one to each (Compose merges it with the base definition):

```yaml
  app:
    extends:
      file: docker-compose.base.yml
      service: app
    image: ...                      # keep the existing line
    volumes:
      - "./docker/mail_templates:/opt/iris/mail_templates:ro"

  worker:
    extends:
      file: docker-compose.base.yml
      service: worker
    image: ...                      # keep the existing line
    volumes:
      - "./docker/mail_templates:/opt/iris/mail_templates:ro"
```

Put your `.html` templates into `docker/mail_templates/` next to the
compose file, recreate the containers and set `mail_templates_dir` to
`/opt/iris/mail_templates`:

```bash
docker compose up -d
```

### Updating

Pull the new version and run the script again – `--force-reinstall`
replaces the installed wheel:

```bash
git pull
./buildnpush2iris.sh -a
```

If the release changes hooks or configuration parameters (see the
release notes), remove the module in *Advanced → Modules* and add it
again, so IRIS registers the new hooks and parameter definitions. Note
down your configuration values first – they are reset to the defaults.

**Upgrading from 1.0.0 to 1.1.0 requires this step:** the send dialog
was replaced by the preview/send/test-send hooks, and the parameter
`manual_hook_sends_with_defaults` was replaced by
`require_preview_before_send`.

## Customer contacts (CISO)

The module derives the `To` recipients from the **contacts configured
on the customer** – no custom attribute is required.

Setup in IRIS: *Customers → \<customer\> → Contacts → Add contact* and
fill in at least:

| Field | Value |
|---|---|
| **Contact name** | e.g. `Jane Doe` |
| **Contact role** | `CISO` |
| **Contact email** | e.g. `jane.doe@customer.example` |

Every contact of that customer whose **Contact role** is `CISO` and
that has an email address receives the report. Multiple CISO contacts
are supported – all of them are addressed.

Details of the matching:

- Role matching is **case-insensitive and whitespace-trimmed, but
  exact**: `CISO`, `ciso` and `  CISO  ` match, `Deputy CISO` does not.
- To address additional roles, extend the configuration parameter
  `customer_contact_roles`, e.g. `CISO, Deputy CISO`.
- Contacts **without** an email address are skipped silently (they are
  simply not recipients).
- Contacts with an **invalid** email address are skipped as well; the
  remaining valid CISO contacts still receive the mail, and the skipped
  ones are listed in the preview and send notes.
- If **no** CISO contact with a valid email address exists, nothing is
  sent and a `FAILED` note is created.

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
| `default_cc` | CSV | no | – | fixed CC for every customer mail |
| `default_bcc` | CSV | no | – | fixed BCC for every customer mail |
| `test_mode_enabled` | bool | yes | false | send only to the test recipients |
| `test_mode_recipients` | CSV | no* | – | *mandatory when test mode is on; also used by the test-send hook |
| `customer_contact_roles` | CSV | yes | `CISO` | contact roles that receive the report |
| `notes_directory_name` | string | yes | `Communication` | notes directory |
| `default_subject_template` | string | yes | `Investigation Report – {{ case.name }} ({{ case.soc_id }})` | subject template |
| `allowed_report_formats` | CSV | yes | `docx,html` | only `docx`/`html` |
| `allowed_report_templates` | CSV | no | – | names/ids; empty = all |
| `allowed_mail_templates` | CSV | no | – | file names; empty = all |
| `mail_templates_dir` | string | no | – | own HTML mail templates; empty ⇒ the ones shipped with the module |
| `default_mail_template` | string | no | `standard_customer_mail.html` | used unless the case sets its own |
| `default_report_template` | string | no** | – | **needed unless every case sets its own |
| `default_report_format` | string | no | `docx` | used unless the case sets its own |
| `require_preview_before_send` | bool | no | true | production sends need a matching preview note |

The configuration is validated strictly on every hook call
(`config_service`); invalid values (e.g. a broken CC address) block
sending and are reported as a note in the case.

## Per-case send options

By default every case uses the module defaults. To let analysts choose
per case, add a **case custom attribute** tab. *Advanced → Custom
Attributes → Case*, add:

```json
{
    "Customer report": {
        "Mail template": {"type": "input_string", "mandatory": false, "value": ""},
        "Report template": {"type": "input_string", "mandatory": false, "value": ""},
        "Report format": {"type": "input_select", "mandatory": false, "options": ["docx", "html"], "value": ""},
        "Mail subject": {"type": "input_string", "mandatory": false, "value": ""}
    }
}
```

The tab name is free; the four **field labels** are what the module
looks for (case-insensitive, in any tab). Empty fields fall back to the
module defaults. `Mail subject` replaces the rendered subject as plain
text. New cases get the tab automatically; to add it to existing cases,
use the option in IRIS to apply the attributes to existing objects.

Changing any of these fields after a preview invalidates it – run the
preview again before sending.

## Mail templates

- A default template (`standard_customer_mail.html`) ships inside the
  wheel and is used when `mail_templates_dir` is empty.
- To use your own: HTML files (`.html`/`.htm`) in the directory set as
  `mail_templates_dir` (see [Installation](#installation) step 4);
  multiple templates are supported and chosen per case.
- **Same template syntax as the IRIS report templates** (Jinja2:
  `{{ … }}`, `{% … %}`).
- Supported variables (at minimum):
  `case.name`, `case.description`, `case.open_date`,
  `case.for_customer`, `case.soc_id`.
  - `case.for_customer` is mapped to the **name of the case customer**
    (IRIS data model: `Cases.client → Client.name`); template
    compatibility is preserved.
  - Additionally available: `case.customer.name`,
    `case.customer.attributes` (customer custom attributes) and
    `case.customer.contacts` (list of all customer contacts with
    `name`, `email` and `role` – e.g. for a personalised salutation).
- Rendering is **sandboxed** with `StrictUndefined` (missing variables
  ⇒ hard error) and **autoescape** (case data cannot inject HTML/JS).
- The subject is rendered from `default_subject_template` unless the
  case sets `Mail subject`. Multilingualism is handled via the templates
  themselves (no language logic inside the module).

## Recipient logic

1. `To` = all contacts of the case customer whose **Contact role**
   matches `customer_contact_roles` (default `CISO`).
2. Role matching is case-insensitive and trimmed, but exact.
3. Contacts without an email address are skipped silently.
4. Contacts with an invalid email address are skipped and reported;
   the remaining valid contacts still receive the mail.
5. If no contact with the configured role has a valid email address,
   the send is **blocked**.
6. Deduplicated case-insensitively (first spelling wins).
7. `CC`/`BCC` exclusively from the module configuration; an invalid
   address there **blocks** the send (it is a configuration error).
8. Recipients are assembled **server-side** on every preview and send –
   analysts cannot change them, only the customer contacts and the
   module configuration determine them.

## Notes / documentation

Every preview and every send attempt creates exactly one note in the
`Communication` directory (created automatically when missing):

| Title | Meaning |
|---|---|
| `PREVIEW – Customer mail YYYY-MM-DD HH:MM – <Customer>` | preview, nothing sent |
| `PREVIEW FAILED – Customer mail YYYY-MM-DD HH:MM – <Customer>` | preview could not be rendered |
| `Customer mail YYYY-MM-DD HH:MM – <Customer>` | mail sent |
| `FAILED – Customer mail YYYY-MM-DD HH:MM – <Customer>` | send attempt failed, nothing sent |

- Content: timestamp, analyst, final To/CC/BCC, skipped contacts, final
  subject, mail and report template, the rendered mail body, the
  content fingerprint and error details if any. Test sends additionally
  list the production recipients that were **not** contacted.
- The rendered report is stored as a **real file** in the case
  **datastore** and linked in the note (IRIS notes have no native
  attachment field; the datastore is the IRIS-conformant location for
  files on a case).
- Titles are shortened to IRIS' limit of 155 characters (long customer
  names are truncated with `…`).
- No separate status field – the state is visible via title and content
  only.

## Error behaviour

Every failure produces a `FAILED` / `PREVIEW FAILED` note in the case –
the only feedback channel, since IRIS just shows *"Queued task"*:

- no matching preview for a production send (*"No matching preview
  found"*) – run the preview (again);
- incomplete or invalid **module configuration** (e.g. missing
  `smtp_host`) – the note tells the analyst to contact an IRIS
  administrator;
- no customer · customer without contacts · no contact with role
  `CISO` · no CISO contact with a (valid) email address · invalid
  address in CC/BCC;
- no report template selected · report/mail template not renderable
  (**hard blocker**) · invalid report format;
- SMTP unreachable · authentication failed · TLS error · timeout.

Individual CISO contacts with an invalid email address do **not** fail
the send: they are skipped, the remaining valid contacts receive the
report, and the skipped ones are listed in the notes.

If the report cannot be stored in the datastore, the note is still
created and documents the problem. If the note itself fails **after a
successful send**, the result stays "sent" and the hook result asks the
analyst to document manually (visible in the module log).

## Security

- SMTP password: `sensitive_string`, **never** logged and **never**
  written into notes (masking filter across all logs/error texts).
- Recipients are assembled server-side from customer contacts and module
  configuration only.
- The preview gate prevents sending content no one has reviewed: the
  send is bound to a preview note by a fingerprint over recipients,
  subject, mail body, templates and format.
- Jinja2 sandbox + StrictUndefined + autoescape; template names are
  checked against the directory listing (no path traversal).
- The subject is normalised to a single line (no header injection).
- The module adds no web routes or endpoints of its own – it only uses
  IRIS' manual hooks, so IRIS' access control applies unchanged.

## Tests

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
make test          # or: python3 -m pytest
```

90 tests, runnable without a running IRIS (IRIS access is encapsulated
in the `iris_adapter` and replaced by an in-memory fake in the tests).
Covered among others: CISO contact selection and role matching, invalid
contacts skipped and reported, preview notes, the preview gate
(blocked without preview, invalidated by changed case data, recipients
or send options, disabled via configuration, not applied to test
sends), per-case send options, test mode, SMTP with/without auth and
TLS on/off, template and report rendering, note titles incl. the 155
character limit, attachment handling, failure notes for every error
path, and notes for configuration errors.

`tests/test_module_packaging.py` additionally guards the parts that
would only break inside IRIS or after packaging: the
`__iris_module_interface` discovery contract, the module configuration
definition, and that the mail templates ship inside the wheel and
render under `StrictUndefined`.

The IRIS adapter itself cannot be unit-tested without IRIS. It was
verified end-to-end against a live IRIS v2.4.29 (preview, blocked send
without preview, send, test send, configuration error; report
generation, datastore attachment and SMTP delivery).

## Assumptions & limitations

All IRIS-specific assumptions are located **only** in
`customer_case_mailer/iris_adapter.py` (documented at the top of the
file):

1. **Synchronous hooks.** The hooks are registered with
   `run_asynchronously=False`. IRIS does not pass the triggering user to
   asynchronous hook handlers, and the IRIS reporter reads
   `current_user` while generating a report – both only work inside the
   analyst's request. Report generation and SMTP delivery therefore run
   in that request (bounded by `smtp_timeout_seconds`).
2. **No dialog.** IRIS 2.4 offers modules no supported way to add web
   pages: with Flask 2.3, routes cannot be registered once the app
   serves requests, and IRIS instantiates modules only on demand. The
   preview/send flow uses notes instead.
3. **Result feedback via notes.** The manual-hook route answers only
   *"Queued task"*; results and errors are written into the case.
4. **Customer contacts:** read from `app.models.models.Contact` via
   `client_id` (`contact_name`, `contact_email`, `contact_role` – a
   free-text field).
5. **Note attachment:** a datastore file + link inside the note (IRIS
   notes have no native attachments).
6. **Module discovery:** the package declares
   `__iris_module_interface = "IrisCustomerCaseMailerInterface"` in its
   `__init__.py`. IRIS imports `<package>.<that value>` and instantiates
   the class of the same name – attribute, file name and class name must
   stay in sync (guarded by `tests/test_module_packaging.py`).

## License

This project is licensed under the **Apache License, Version 2.0** –
see the [LICENSE](LICENSE) file for the full license text.

```
Copyright 2026 Niklas Thürnau

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
