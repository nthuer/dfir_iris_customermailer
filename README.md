# iris_customer_case_mailer_module

DFIR-IRIS **processor module** (`customer_case_mailer`) for IRIS **>= 2.4.27**.

Analysts send a customer-ready **investigation report by email**
directly from a case – without leaving IRIS. Recipients are derived
from the **contacts configured on the customer**: everyone whose
contact role is **CISO** and who has an email address. Every send
attempt (success or failure) is automatically documented as a **note in
the `Communication` directory**, including the actually sent report as
a file.

---

## Contents

- [Features](#features)
- [Architecture & data flow](#architecture--data-flow)
- [Project structure](#project-structure)
- [Installation](#installation)
- [Customer contacts (CISO)](#customer-contacts-ciso)
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
   2. iris_adapter      │  loads case + customer + customer contacts
   3. recipient_service │  pick contacts with role CISO, validate, dedupe,
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
buildnpush2iris.sh                     # build the wheel + install into IRIS
Makefile                               # make test / build / install / clean
setup.py, MANIFEST.in                  # packaging (wheel incl. templates)
iris_customer_case_mailer_module/
├── __init__.py                        # declares __iris_module_interface
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
    ├── mail_templates/                # default HTML mail templates (shipped)
    └── ui/
        ├── blueprint.py               # send dialog (Flask blueprint)
        └── templates/dialog.html      # dialog frontend (vanilla JS)
tests/                                 # 71 unit/flow tests (run without IRIS)
```

## Installation

Installation follows the usual DFIR-IRIS module flow: build a wheel and
install it into the IRIS containers, then register the module in the web
interface.

**Requirements:** a running DFIR-IRIS (>= 2.4.27) deployment, `docker`,
and `python3` with `wheel` (or `build`) on the host.

### 1. Clone and install

```bash
git clone https://github.com/nthuer/dfir_iris_customermailer
cd dfir_iris_customermailer
./buildnpush2iris.sh -a
```

The script builds the wheel, copies it to `/iriswebapp/dependencies/` in
the containers, installs it with `pip3 install --force-reinstall` and
restarts them.

**Always use `-a`**: it installs into the worker *and* the app
container. This module needs both – the manual hook runs in the worker,
the send dialog is served by the app. Without a flag only the worker is
updated.

Non-standard container names can be overridden:

```bash
IRIS_APP_CONTAINER=my_app IRIS_WORKER_CONTAINER=my_worker ./buildnpush2iris.sh -a
```

### 2. Register the module in IRIS

1. *Advanced → Modules → Add module*
2. Module name: `iris_customer_case_mailer_module`
3. Fill in the configuration (see [Module
   configuration](#module-configuration); `smtp_host`, `smtp_port` and
   `smtp_from_address` are mandatory)
4. **Enable** the module

The hook then shows up inside a case under *Actions → Send customer
report*.

### 3. Optional: use your own mail templates

The module ships with a default HTML mail template, so it works right
after installation. To use your own, mount a directory into **both**
containers and point `mail_templates_dir` at it.

In `/opt/iris-web/docker-compose.yml`, add the volume to the `app` and
the `worker` service:

```yaml
      - "./docker/mail_templates:/opt/iris/mail_templates:ro"
```

Then restart IRIS and set `mail_templates_dir` to
`/opt/iris/mail_templates` in the module configuration:

```bash
cd /opt/iris-web && docker compose down && docker compose up -d
```

### Updating

Pull the new version and run the script again – `--force-reinstall`
replaces the installed wheel:

```bash
git pull
./buildnpush2iris.sh -a
```

If the set of configuration parameters changed, remove and re-add the
module in *Advanced → Modules* so IRIS picks up the new definition.

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
  ones are listed in the dialog and in the documentation note.
- If **no** CISO contact with a valid email address exists, the send is
  blocked with an error and a `FAILED` note is created.

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
| `customer_contact_roles` | CSV | yes | `CISO` | contact roles that receive the report |
| `notes_directory_name` | string | yes | `Communication` | notes directory |
| `default_subject_template` | string | yes | `Investigation Report – {{ case.name }} ({{ case.soc_id }})` | subject template |
| `allowed_report_formats` | CSV | yes | `docx,html` | only `docx`/`html` |
| `allowed_report_templates` | CSV | no | – | names/ids; empty = all |
| `allowed_mail_templates` | CSV | no | – | file names; empty = all |
| `mail_templates_dir` | string | no | – | own HTML mail templates; empty ⇒ the ones shipped with the module |
| `default_mail_template` | string | no | `standard_customer_mail.html` | preselection/hook send |
| `default_report_template` | string | no | – | preselection/hook send |
| `default_report_format` | string | no | `docx` | preselection/hook send |
| `manual_hook_sends_with_defaults` | bool | no | false | hook sends directly using defaults |

The configuration is validated strictly on load (`config_service`);
invalid values (e.g. a broken CC address) block sending with an
understandable error message.

## Mail templates

- A default template (`standard_customer_mail.html`) ships inside the
  wheel and is used when `mail_templates_dir` is empty.
- To use your own: HTML files (`.html`/`.htm`) in the directory set as
  `mail_templates_dir` (see [Installation](#installation) step 3);
  multiple templates are supported and selectable in the dialog.
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
8. Recipients are assembled **server-side and final** – the dialog only
   displays them, frontend input for recipients is ignored.

## Notes / documentation

After **every** send attempt exactly one note is created in the
`Communication` directory (created automatically when missing):

- Title on success: `Customer mail YYYY-MM-DD HH:MM – <Customer>`
- Title on failure: `FAILED – Customer mail YYYY-MM-DD HH:MM – <Customer>`
- Content: timestamp, final To/CC/BCC, final subject, analyst identity,
  rendered mail body, skipped contacts with invalid email addresses,
  and error details if any.
- The actually sent report is stored as a **real file** in the case
  **datastore** and linked inside the note (IRIS notes have no native
  attachment field; the datastore is the IRIS-conformant location for
  files on a case).
- No separate status field – success/failure only via title and content.

## Error behaviour

Cleanly handled cases (each with a UI message **and** a failure note):
no customer on the case · customer without any contacts · no contact
with role `CISO` · no CISO contact with an email address · no CISO
contact with a *valid* email address · invalid address in CC/BCC ·
report/mail template not renderable (**hard blocker**) · SMTP
unreachable · authentication failed · TLS error · timeout · note
directory/note not creatable · attachment not storable (documented
inside the note, does not abort the documentation).

Individual CISO contacts with an invalid email address do **not** fail
the send: they are skipped, the remaining valid contacts receive the
report, and the skipped ones are listed in the dialog and in the note.

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
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
make test          # or: python3 -m pytest
```

71 tests, runnable without a running IRIS (IRIS access is encapsulated
in the `iris_adapter` and replaced by an in-memory fake in the tests).
Covered among others: single/multiple CISO contacts, contacts with
other roles being ignored, case-insensitive/exact role matching,
configurable roles, contacts without email skipped, invalid contact
email skipped and reported, no CISO contact blocking the send,
trim/dedupe, SMTP with/without auth, TLS on/off, test mode override,
mail template rendering with `case.*`, report rendering DOCX/HTML,
mail/report preview, automatic directory creation, one note per send,
attachment on the note, failure note on failed send, hard template
errors.

`tests/test_module_packaging.py` additionally guards the parts that
would only break inside IRIS or after packaging: the
`__iris_module_interface` discovery contract (package attribute,
submodule file name and class name must agree), the module
configuration definition, and that the mail templates actually ship
inside the wheel and render under `StrictUndefined`.

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
2. **Customer contacts:** read from the IRIS model
   `app.models.models.Contact` (table `contact`) via `client_id`, using
   the fields `contact_name`, `contact_email` and `contact_role`
   ("Contact Role" in the IRIS UI, a free-text field).
3. **Reporter signatures:** `IrisMakeDocReport`/`IrisMakeMdReport`
   vary between minor versions; the adapter uses feature detection.
   On deviations adapt only the adapter.
4. **Note attachment:** realised as a datastore file + link inside the
   note (IRIS-conformant, since notes have no native attachments).
5. **HTML report preview** opens in a new tab; the DOCX preview is
   delivered as a download (browsers cannot render DOCX natively).
6. **Module discovery:** the package declares
   `__iris_module_interface = "IrisCustomerCaseMailerInterface"` in its
   `__init__.py`. IRIS imports `<package>.<that value>` and instantiates
   the class of the same name, so the attribute, the file name and the
   class name must stay in sync (guarded by
   `tests/test_module_packaging.py`).
7. `buildnpush2iris.sh` restarts both containers itself. After changing
   the module **configuration** in the IRIS UI a restart may still be
   required for it to take effect.

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
