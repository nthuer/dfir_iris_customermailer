"""IRIS adapter: the only place that touches IRIS internals.

All services work exclusively against this class. This keeps the rest
of the module testable without a running IRIS (tests inject a fake
adapter), and IRIS version deviations only ever require changes here.

VERIFIED against the DFIR-IRIS v2.4.29 source and a live v2.4.29
instance (on deviations adapt ONLY this file):
- Modules run inside the IRIS process and may import ``app.*``. The
  manual hooks are registered synchronously, so the handler runs in the
  analyst's web request: ``flask_login.current_user`` is the analyst,
  and the IRIS reporter (which reads ``current_user.name``) works.
- ``app.models.cases.Cases``: name, description, open_date, soc_id,
  client_id (NOT NULL) and ``custom_attributes`` (JSON).
- Customer: ``app.models.models.Client``; contacts:
  ``app.models.models.Contact`` with client_id, contact_name,
  contact_email and the free-text ``contact_role``.
- Users: ``app.models.authorization.User`` (fields ``user``, ``name``).
- Report templates: ``CaseTemplateReport`` (``internal_reference`` holds
  the stored file name, the extension tells the format) + ``ReportType``
  named "Investigation".
- Reporter: ``IrisMakeDocReport.generate_doc_report(doc_type)`` and
  ``IrisMakeMdReport.generate_md_report(doc_type)``, both constructed
  with ``(tmp_dir, report_id, caseid, safe_mode)``; they return
  ``(path, message)`` or ``None`` / ``(None, error)``.
- Notes: ``NoteDirectory`` (name, parent_id, case_id),
  ``case_notes_db.add_note(note_title, creation_date, user_id, caseid,
  directory_id, note_content)`` which commits itself. Note titles are
  limited to 155 characters.
- Datastore: ``datastore_get_root(cid)`` (initialises the tree),
  ``datastore_get_standard_path(dsf, cid)`` (needs ``file_uuid``).
  ``DataStoreFile.file_local_name`` must hold the FULL path – IRIS
  checks it lies inside the datastore before serving the file – and
  ``added_by_user_id`` is NOT NULL. Files are served at
  ``/datastore/file/view/<file_id>?cid=<case_id>``.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from .errors import AdapterError, AttachmentError, NotesError, ReportRenderError
from .models import CaseContext, CustomerContact

PACKAGE_NAME = "iris_customer_case_mailer_module"  # module_name column in IRIS

_DOCX_EXT = (".docx",)
_HTML_EXT = (".html", ".htm", ".md")

# Case custom attribute field labels -> send option keys (case-insensitive).
CASE_OPTION_FIELDS = {
    "mail template": "mail_template",
    "report template": "report_template",
    "report format": "report_format",
    "mail subject": "subject",
}


def extract_case_send_options(custom_attributes: Any) -> Dict[str, str]:
    """Reads the per-case send options from case custom attributes.

    IRIS stores custom attributes as ``{Tab: {Field: {"value": ...}}}``.
    Fields are matched by label (case-insensitive) in any tab; empty
    values are ignored so the module defaults apply.
    """
    options: Dict[str, str] = {}
    if not isinstance(custom_attributes, dict):
        return options
    for tab in custom_attributes.values():
        if not isinstance(tab, dict):
            continue
        for label, field in tab.items():
            key = CASE_OPTION_FIELDS.get(str(label).strip().lower())
            if key is None:
                continue
            value = field.get("value") if isinstance(field, dict) else field
            if value is not None and str(value).strip():
                options[key] = str(value).strip()
    return options


class IrisAdapter:
    """Production adapter – only works inside IRIS."""

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _import(module_path: str):
        try:
            import importlib
            return importlib.import_module(module_path)
        except ImportError as exc:
            raise AdapterError(
                f"IRIS internals unavailable ({module_path}). Is the module "
                f"running inside an IRIS instance (>= 2.4.27)?",
                details=repr(exc),
            )

    def _db_session(self):
        return self._import("app").db.session

    # ------------------------------------------------------------ module config

    def get_module_raw_config(self) -> Any:
        """Reads the persisted module configuration from the IRIS DB.

        Fallback only - the interface normally uses IRIS'
        ``module_dict_conf``.
        """
        models = self._import("app.models.models")
        iris_module = (self._db_session().query(models.IrisModule)
                       .filter(models.IrisModule.module_name == PACKAGE_NAME)
                       .first())
        if iris_module is None:
            raise AdapterError(
                f"Module '{PACKAGE_NAME}' is not registered in IRIS. Please "
                f"add it in Advanced -> Modules.")
        return iris_module.module_config

    # ------------------------------------------------------------------ analyst

    def current_analyst(self) -> Tuple[Optional[int], str]:
        """The analyst who triggered the hook (synchronous hooks only)."""
        try:
            from flask_login import current_user
            user_id = getattr(current_user, "id", None)
            if user_id is None:
                return None, "unknown (no logged-in user)"
            login = getattr(current_user, "user", None)
            name = getattr(current_user, "name", None)
            label = f"{name} ({login})" if name and login and name != login else (login or name)
            return user_id, f"{label} (id {user_id})"
        except Exception:  # noqa: BLE001 - outside a request context
            return None, "unknown (no request context)"

    # --------------------------------------------------------------------- case

    def get_case_context(self, case_id: int) -> CaseContext:
        cases_mod = self._import("app.models.cases")
        models = self._import("app.models.models")
        session = self._db_session()

        case = session.query(cases_mod.Cases).filter(cases_mod.Cases.case_id == case_id).first()
        if case is None:
            raise AdapterError(f"Case #{case_id} was not found.")

        customer = None
        if getattr(case, "client_id", None):
            customer = (session.query(models.Client)
                        .filter(models.Client.client_id == case.client_id).first())

        customer_name = None
        customer_attrs: Dict = {}
        contacts: List[CustomerContact] = []
        if customer is not None:
            customer_name = getattr(customer, "name", None)
            raw_attrs = getattr(customer, "custom_attributes", None) or {}
            customer_attrs = raw_attrs if isinstance(raw_attrs, dict) else {}
            contacts = self.get_customer_contacts(customer.client_id)

        open_date = getattr(case, "open_date", None)
        if isinstance(open_date, (datetime, date)):
            open_date_str = open_date.strftime("%Y-%m-%d")
        else:
            open_date_str = str(open_date) if open_date else ""

        return CaseContext(
            case_id=case_id,
            name=getattr(case, "name", "") or "",
            description=getattr(case, "description", "") or "",
            open_date=open_date_str,
            soc_id=getattr(case, "soc_id", "") or "",
            customer_name=customer_name,
            customer_attributes=customer_attrs,
            contacts=contacts,
            send_options=extract_case_send_options(getattr(case, "custom_attributes", None)),
        )

    def get_customer_contacts(self, client_id: int) -> List[CustomerContact]:
        """All contacts configured on a customer (IRIS: Customer -> Contacts)."""
        models = self._import("app.models.models")
        rows = (self._db_session().query(models.Contact)
                .filter(models.Contact.client_id == client_id).all())
        return [
            CustomerContact(
                name=(row.contact_name or "").strip(),
                email=(row.contact_email or "").strip(),
                role=(row.contact_role or "").strip(),
            )
            for row in rows
        ]

    # ---------------------------------------------------------------- reporting

    def list_investigation_report_templates(self) -> List[Dict]:
        models = self._import("app.models.models")
        query = (self._db_session().query(models.CaseTemplateReport, models.ReportType)
                 .join(models.ReportType,
                       models.CaseTemplateReport.report_type_id == models.ReportType.id)
                 .filter(models.ReportType.name.ilike("Investigation%")))

        templates = []
        for template, _rtype in query.all():
            ext = os.path.splitext(str(template.internal_reference or ""))[1].lower()
            if ext in _DOCX_EXT:
                fmt = "docx"
            elif ext in _HTML_EXT:
                fmt = "html"
            else:
                continue  # unknown template format -> do not offer
            templates.append({
                "id": template.id,
                "name": template.name,
                "description": template.description or "",
                "format": fmt,
            })
        return templates

    def generate_report(self, case_id: int, template_id: int,
                        report_format: str, user_id: Optional[int]) -> bytes:
        """Renders via the IRIS reporter and returns the file bytes."""
        reporter_mod = self._import("app.iris_engine.reporter.reporter")
        if report_format == "docx":
            reporter_cls, method_name = reporter_mod.IrisMakeDocReport, "generate_doc_report"
        else:
            reporter_cls, method_name = reporter_mod.IrisMakeMdReport, "generate_md_report"

        tmp_dir = tempfile.mkdtemp(prefix="ccm_report_")
        try:
            reporter = reporter_cls(tmp_dir, template_id, case_id, False)
            result = getattr(reporter, method_name)("Investigation")

            if isinstance(result, (tuple, list)):
                report_path, message = (list(result) + [None])[:2]
            else:
                report_path, message = result, None
            if not report_path or not os.path.isfile(str(report_path)):
                raise ReportRenderError(
                    "The IRIS reporter did not produce a report file"
                    + (f": {message}" if message else " (template broken or not renderable)."))

            with open(str(report_path), "rb") as fh:
                return fh.read()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # -------------------------------------------------------------------- notes

    def ensure_note_directory(self, case_id: int, name: str,
                              user_id: Optional[int]) -> int:
        """Returns the id of the note directory, creating it when missing."""
        models = self._import("app.models.models")
        directory_cls = models.NoteDirectory
        session = self._db_session()
        existing = (session.query(directory_cls)
                    .filter(directory_cls.case_id == case_id,
                            directory_cls.name == name,
                            directory_cls.parent_id.is_(None))
                    .first())
        if existing is not None:
            return existing.id

        try:
            directory = directory_cls(name=name, parent_id=None, case_id=case_id)
            session.add(directory)
            session.commit()
            return directory.id
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            raise NotesError(
                f"Note directory '{name}' could not be created: {exc}",
                details=repr(exc))

    def add_note(self, case_id: int, directory_id: int, title: str,
                 content: str, user_id: Optional[int]) -> int:
        notes_db = self._import("app.datamgmt.case.case_notes_db")
        try:
            note = notes_db.add_note(
                note_title=title,
                creation_date=datetime.utcnow(),
                user_id=user_id,
                caseid=case_id,
                directory_id=directory_id,
                note_content=content,
            )
        except Exception as exc:  # noqa: BLE001
            self._db_session().rollback()
            raise NotesError(f"Note could not be saved: {exc}", details=repr(exc))
        if note is None:
            raise NotesError("Note could not be saved (IRIS returned None).")
        return note.note_id

    def note_exists(self, case_id: int, title_prefix: str,
                    content_fragment: str) -> bool:
        """True if a note of the case starts with the title prefix and
        contains the fragment (used for the preview gate)."""
        models = self._import("app.models.models")
        notes = models.Notes
        found = (self._db_session().query(notes.note_id)
                 .filter(notes.note_case_id == case_id,
                         notes.note_title.startswith(title_prefix, autoescape=True),
                         notes.note_content.contains(content_fragment, autoescape=True))
                 .first())
        return found is not None

    # ---------------------------------------------------------------- datastore

    def store_report_in_datastore(self, case_id: int, filename: str,
                                  content: bytes,
                                  user_id: Optional[int]) -> Tuple[int, str]:
        """Stores the report as a real file in the case datastore.

        Returns (file_id, link) – the link is referenced in the note.
        """
        if user_id is None:
            raise AttachmentError(
                "No analyst could be determined - IRIS requires an owner for "
                "datastore files.")

        models = self._import("app.models.models")
        ds_db = self._import("app.datamgmt.datastore.datastore_db")
        session = self._db_session()

        root = ds_db.datastore_get_root(case_id)
        if root is None:
            raise AttachmentError("Datastore of the case could not be initialised.")

        written_path = None
        try:
            dsf = models.DataStoreFile(
                file_uuid=uuid.uuid4(),   # needed before insert to compute the path
                file_original_name=filename,
                file_description="Investigation report sent via customer_case_mailer",
                file_case_id=case_id,
                file_date_added=datetime.utcnow(),
                file_parent_id=root.path_id,
                added_by_user_id=user_id,
                file_size=len(content),
                file_is_ioc=False,
                file_is_evidence=False,
                file_sha256=hashlib.sha256(content).hexdigest(),
            )
            local_path = ds_db.datastore_get_standard_path(dsf, case_id)
            with open(local_path, "wb") as fh:
                fh.write(content)
            written_path = local_path

            dsf.file_local_name = str(local_path)   # full path, see module doc
            session.add(dsf)
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            if written_path is not None:
                try:
                    os.remove(written_path)
                except OSError:
                    pass
            raise AttachmentError(
                f"Report could not be stored in the datastore: {exc}",
                details=repr(exc))

        return dsf.file_id, f"/datastore/file/view/{dsf.file_id}?cid={case_id}"
