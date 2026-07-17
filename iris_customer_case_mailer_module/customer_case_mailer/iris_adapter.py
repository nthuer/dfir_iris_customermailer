"""IRIS adapter: the only place that touches IRIS internals.

All services work exclusively against this class. This keeps the rest
of the module testable without a running IRIS (tests inject a fake
adapter), and IRIS version deviations only ever require changes here.

ASSUMPTIONS (checked against DFIR-IRIS v2.4.27 – on deviations adapt
ONLY this file):
- Modules run inside the IRIS process (webapp/worker) and may import
  ``app.*``.
- ``app.models.cases.Cases`` with fields name, description, open_date,
  soc_id, client_id; the customer is ``app.models.models.Client`` with
  ``custom_attributes`` (JSON, structure: {Tab: {Field: {value: ...}}}).
- Report templates: ``app.models.models.CaseTemplateReport`` +
  ``ReportType`` (name "Investigation"); format detection via the file
  extension of the stored template.
- Reporter: ``app.iris_engine.reporter.reporter`` with
  ``IrisMakeDocReport`` (DOCX/docxtpl) and ``IrisMakeMdReport``
  (Markdown/HTML). Signatures are called defensively via feature
  detection because they vary between minor versions.
- Notes: ``app.models.models.NotesDirectory`` (v2.4 directories) and
  ``app.datamgmt.case.case_notes_db.add_note``.
- Datastore: files on a case live in the datastore; links have the form
  ``/datastore/file/view/<id>?cid=<case_id>``.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .errors import AdapterError, AttachmentError, NotesError, ReportRenderError
from .models import CaseContext

MODULE_NAME = "IrisCustomerCaseMailer"

_DOCX_EXT = (".docx",)
_HTML_EXT = (".html", ".htm", ".md")


def _extract_custom_attribute(custom_attributes: Any, attribute_name: str) -> Optional[str]:
    """Looks up a custom attribute in the (possibly nested) IRIS structure.

    IRIS stores custom attributes as ``{Tab: {Field: {"value": ...}}}``.
    Flat dicts (``{Field: value}``) are supported additionally.
    """
    if not isinstance(custom_attributes, dict):
        return None
    # Flat structure
    if attribute_name in custom_attributes:
        value = custom_attributes[attribute_name]
        if isinstance(value, dict):
            value = value.get("value")
        return None if value is None else str(value)
    # Nested via tabs
    for tab in custom_attributes.values():
        if isinstance(tab, dict) and attribute_name in tab:
            field = tab[attribute_name]
            if isinstance(field, dict):
                field = field.get("value")
            return None if field is None else str(field)
    return None


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
        app_module = self._import("app")
        return app_module.db.session

    # ------------------------------------------------------------- module config

    def get_module_raw_config(self, module_name: str = MODULE_NAME) -> Any:
        """Reads the persisted module configuration from the IRIS DB."""
        models = self._import("app.models.models")
        iris_module = (self._db_session().query(models.IrisModule)
                       .filter(models.IrisModule.module_name == module_name)
                       .first())
        if iris_module is None:
            raise AdapterError(
                f"Module '{module_name}' is not registered in IRIS. Please "
                f"add it via the module management and restart the services.")
        return iris_module.module_config

    # ------------------------------------------------------------------- case

    def get_case_context(self, case_id: int, email_attribute: str) -> CaseContext:
        cases_mod = self._import("app.models.cases")
        models = self._import("app.models.models")

        case = (self._db_session().query(cases_mod.Cases)
                .filter(cases_mod.Cases.case_id == case_id).first())
        if case is None:
            raise AdapterError(f"Case #{case_id} was not found.")

        customer = None
        if getattr(case, "client_id", None):
            customer = (self._db_session().query(models.Client)
                        .filter(models.Client.client_id == case.client_id).first())

        contact_raw = None
        customer_name = None
        customer_attrs: Dict = {}
        if customer is not None:
            customer_name = getattr(customer, "name", None)
            raw_attrs = getattr(customer, "custom_attributes", None) or {}
            customer_attrs = raw_attrs if isinstance(raw_attrs, dict) else {}
            contact_raw = _extract_custom_attribute(customer_attrs, email_attribute)

        open_date = getattr(case, "open_date", None)
        if isinstance(open_date, datetime):
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
            contact_emails_raw=contact_raw,
        )

    def get_user_display(self, user_id: Optional[int]) -> str:
        if user_id is None:
            return "unknown"
        try:
            models = self._import("app.models.models")
            user = (self._db_session().query(models.User)
                    .filter(models.User.id == user_id).first())
            if user is not None:
                name = getattr(user, "user", None) or getattr(user, "name", None)
                return f"{name} (id {user_id})"
        except AdapterError:
            pass
        return f"user id {user_id}"

    # --------------------------------------------------------------- reporting

    def list_investigation_report_templates(self) -> List[Dict]:
        models = self._import("app.models.models")
        session = self._db_session()

        query = (session.query(models.CaseTemplateReport, models.ReportType)
                 .join(models.ReportType,
                       models.CaseTemplateReport.report_type_id == models.ReportType.id)
                 .filter(models.ReportType.name.ilike("Investigation%")))

        templates = []
        for template, _rtype in query.all():
            filename = (getattr(template, "internal_reference", None)
                        or getattr(template, "filename", "") or "")
            ext = os.path.splitext(str(filename))[1].lower()
            if ext in _DOCX_EXT:
                fmt = "docx"
            elif ext in _HTML_EXT:
                fmt = "html"
            else:
                continue  # unknown template format -> do not offer
            templates.append({
                "id": template.id,
                "name": template.name,
                "description": getattr(template, "description", "") or "",
                "format": fmt,
            })
        return templates

    def generate_report(self, case_id: int, template_id: int,
                        report_format: str, user_id: Optional[int]) -> bytes:
        """Renders via the IRIS reporter and returns the file bytes.

        Reporter signatures vary slightly between IRIS versions, hence
        feature detection instead of a hard signature.
        """
        reporter_mod = self._import("app.iris_engine.reporter.reporter")
        cls_name = "IrisMakeDocReport" if report_format == "docx" else "IrisMakeMdReport"
        reporter_cls = getattr(reporter_mod, cls_name, None)
        if reporter_cls is None:
            raise ReportRenderError(
                f"IRIS reporter '{cls_name}' not found – "
                f"check the IRIS version (expected >= 2.4.27).")

        tmp_dir = tempfile.mkdtemp(prefix="ccm_report_")
        try:
            try:
                reporter = reporter_cls(tmp_dir, template_id, case_id, False)
            except TypeError:
                reporter = reporter_cls(tmp_dir, template_id, case_id)

            method = (getattr(reporter, "generate_doc_report", None)
                      or getattr(reporter, "generate_md_report", None)
                      or getattr(reporter, "generate_report", None))
            if method is None:
                raise ReportRenderError(
                    f"IRIS reporter '{cls_name}' offers no known "
                    f"generation method.")

            try:
                result = method("Investigation")
            except TypeError:
                result = method()

            # Normalise result: path or (path, logs).
            report_path = result[0] if isinstance(result, (tuple, list)) else result
            if not report_path or not os.path.isfile(str(report_path)):
                raise ReportRenderError(
                    "The IRIS reporter did not produce a report file "
                    "(template broken or not renderable).")

            with open(str(report_path), "rb") as fh:
                return fh.read()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ------------------------------------------------------------------- notes

    def ensure_note_directory(self, case_id: int, name: str,
                              user_id: Optional[int]) -> int:
        """Returns the id of the note directory, creating it when missing."""
        models = self._import("app.models.models")
        directory_cls = (getattr(models, "NotesDirectory", None)
                         or getattr(models, "NoteDirectory", None))
        if directory_cls is None:
            raise NotesError(
                "IRIS model for note directories not found – "
                "note directories require IRIS >= 2.4.")

        session = self._db_session()
        existing = (session.query(directory_cls)
                    .filter(directory_cls.case_id == case_id,
                            directory_cls.name == name)
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
        add_note_fn = getattr(notes_db, "add_note", None)
        if add_note_fn is None:
            raise NotesError("IRIS function case_notes_db.add_note not found.")
        try:
            note = add_note_fn(
                note_title=title,
                creation_date=datetime.utcnow(),
                user_id=user_id,
                caseid=case_id,
                note_content=content,
                directory_id=directory_id,
            )
        except TypeError:
            # Older signature without keyword support
            note = add_note_fn(title, datetime.utcnow(), user_id, case_id,
                               directory_id, content)
        except Exception as exc:  # noqa: BLE001
            self._db_session().rollback()
            raise NotesError(f"Note could not be saved: {exc}",
                             details=repr(exc))
        if note is None:
            raise NotesError("Note could not be saved (IRIS returned None).")
        return getattr(note, "note_id", getattr(note, "id", 0))

    # --------------------------------------------------------------- datastore

    def store_report_in_datastore(self, case_id: int, filename: str,
                                  content: bytes,
                                  user_id: Optional[int]) -> Tuple[int, str]:
        """Stores the report as a real file in the case datastore.

        Returns (file_id, link) – the link is referenced in the note.
        """
        import hashlib

        models = self._import("app.models.models")
        ds_db = self._import("app.datamgmt.datastore.datastore_db")
        session = self._db_session()

        # Determine the root node of the case datastore (initialise if needed).
        root_getter = (getattr(ds_db, "datastore_get_root", None)
                       or getattr(ds_db, "get_ds_root", None))
        root = root_getter(case_id) if root_getter else None
        if root is None:
            init_fn = getattr(ds_db, "init_ds_tree", None)
            root = init_fn(case_id) if init_fn else None
        if root is None:
            raise AttachmentError(
                "Datastore root directory of the case not found – "
                "report cannot be stored.")
        root_id = getattr(root, "path_id", getattr(root, "id", None))

        # Ask IRIS for the physical storage path.
        path_fn = (getattr(ds_db, "datastore_get_local_file_path", None)
                   or getattr(ds_db, "datastore_get_standard_path", None))

        try:
            dsf = models.DataStoreFile(
                file_original_name=filename,
                file_description="Investigation report sent via customer_case_mailer",
                file_case_id=case_id,
                file_date_added=datetime.utcnow(),
                file_parent_id=root_id,
                added_by_user_id=user_id,
                file_size=len(content),
                file_is_evidence=False,
            )
            session.add(dsf)
            session.flush()  # file_id is needed for path/link

            if path_fn is None:
                raise AttachmentError(
                    "IRIS datastore path function not found – "
                    "report cannot be stored.")
            local_path = path_fn(dsf, case_id)
            local_path = getattr(local_path, "as_posix", lambda: str(local_path))()

            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, "wb") as fh:
                fh.write(content)

            dsf.file_local_name = os.path.basename(local_path)
            dsf.file_sha256 = hashlib.sha256(content).hexdigest()
            session.commit()
        except AttachmentError:
            session.rollback()
            raise
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            raise AttachmentError(
                f"Report could not be stored in the datastore: {exc}",
                details=repr(exc))

        link = f"/datastore/file/view/{dsf.file_id}?cid={case_id}"
        return dsf.file_id, link
