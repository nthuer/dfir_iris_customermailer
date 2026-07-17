"""Hook layer: IRIS module interface of the customer_case_mailer.

Registers the manual case hook "Send customer report" and processes its
invocations. The actual domain logic lives entirely in
:mod:`customer_case_mailer` – this file is deliberately thin.

Behaviour of the manual hook:
- ``manual_hook_sends_with_defaults`` enabled: direct send using the
  default templates/formats stored in the configuration.
- otherwise: the hook reports the link to the send dialog (the IRIS
  hook framework cannot open parameterised dialogs, see README
  "Assumptions & limitations").
"""

import traceback

import iris_interface.IrisInterfaceStatus as InterfaceStatus
from iris_interface.IrisModuleInterface import IrisModuleInterface, IrisModuleTypes

import iris_customer_case_mailer_module.customer_case_mailer_conf as interface_conf
from iris_customer_case_mailer_module.customer_case_mailer.audit_service import get_logger
from iris_customer_case_mailer_module.customer_case_mailer.config_service import (
    raw_config_from_iris,
)
from iris_customer_case_mailer_module.customer_case_mailer.errors import MailerError
from iris_customer_case_mailer_module.customer_case_mailer.iris_adapter import IrisAdapter
from iris_customer_case_mailer_module.customer_case_mailer.mailer import CustomerCaseMailer
from iris_customer_case_mailer_module.customer_case_mailer.models import SendSelection

DIALOG_URL_PATTERN = "/customer_case_mailer/dialog?cid={case_id}"


class IrisCustomerCaseMailerInterface(IrisModuleInterface):

    name = interface_conf.module_name
    _module_name = interface_conf.module_name
    _module_description = interface_conf.module_description
    _interface_version = interface_conf.interface_version
    _module_version = interface_conf.module_version
    _pipeline_support = interface_conf.pipeline_support
    _pipeline_info = interface_conf.pipeline_info
    _module_configuration = interface_conf.module_configuration
    _module_type = IrisModuleTypes.module_processor

    def __init__(self):
        super().__init__()
        self._ccm_logger = get_logger()
        # Register the send dialog (Flask blueprint) – best effort: not
        # available outside the webapp process (e.g. worker).
        try:
            from iris_customer_case_mailer_module.customer_case_mailer.ui.blueprint import (
                register_ui,
            )
            register_ui()
        except Exception as exc:  # noqa: BLE001
            self._ccm_logger.warning(
                "Send dialog could not be registered "
                "(only hook direct send available): %s" % exc)

    # ------------------------------------------------------------------- hooks

    def register_hooks(self, module_id: int):
        """Registers the manual case hook 'Send customer report'."""
        self.module_id = module_id
        status = self.register_to_hook(
            module_id,
            iris_hook_name="on_manual_trigger_case",
            manual_hook_name="Send customer report",
        )
        if status.is_failure():
            self.log.error(status.get_message())
            self.log.error(status.get_data())
            return status
        self.log.info("Hook 'Send customer report' registered.")
        return InterfaceStatus.I2Success("Hooks registered.")

    def hooks_handler(self, hook_name: str, hook_ui_name: str, data):
        """Processes the manual hook invocation for one or more cases."""
        self.log.info("Hook '%s' (%s) received." % (hook_name, hook_ui_name))
        messages = []
        try:
            raw_config = self._raw_module_config()
            for case in data:
                case_id = getattr(case, "case_id", None)
                if case_id is None:
                    continue
                messages.append(self._handle_case_trigger(raw_config, case_id))
        except MailerError as exc:
            self.log.error(exc.user_message)
            return InterfaceStatus.I2Error(message=exc.user_message, data=data)
        except Exception:  # noqa: BLE001
            self.log.error(traceback.format_exc())
            return InterfaceStatus.I2Error(
                message="Unexpected error in the customer_case_mailer hook "
                        "(details in the module log).",
                data=data)

        summary = " | ".join(messages) if messages else "No cases provided."
        self.log.info(summary)
        return InterfaceStatus.I2Success(message=summary, data=data)

    # ----------------------------------------------------------------- intern

    def _raw_module_config(self):
        """Current module configuration as a flat dict.

        Primarily the configuration held by the interface, falling back
        to a direct read from the IRIS DB (worker context).
        """
        for candidate in (getattr(self, "_dict_conf", None),
                          getattr(self, "module_dict_conf", None)):
            if candidate:
                return raw_config_from_iris(candidate)
        return raw_config_from_iris(IrisAdapter().get_module_raw_config())

    def _handle_case_trigger(self, raw_config, case_id: int) -> str:
        mailer = CustomerCaseMailer(raw_config, logger=self._ccm_logger)

        if not mailer.config.manual_hook_sends_with_defaults:
            url = DIALOG_URL_PATTERN.format(case_id=case_id)
            return (f"Case #{case_id}: open the send dialog: {url} "
                    "(direct send via hook is disabled).")

        cfg = mailer.config
        if not (cfg.default_mail_template and cfg.default_report_template):
            return (f"Case #{case_id}: direct send not possible – "
                    "'default_mail_template'/'default_report_template' are "
                    "not configured.")

        # The hook does not provide the triggering user; the send is
        # documented as a system/hook send.
        selection = SendSelection(
            mail_template=cfg.default_mail_template,
            report_template=cfg.default_report_template,
            report_format=cfg.default_report_format,
        )
        result = mailer.send(case_id, analyst_id=None,
                             analyst_display="IRIS hook (Send customer report)",
                             selection=selection)
        return f"Case #{case_id}: {result.message}"
