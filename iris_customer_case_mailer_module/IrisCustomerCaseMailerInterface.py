"""Hook layer: IRIS module interface of the customer_case_mailer.

Registers three manual case hooks and delegates to the domain logic in
:mod:`customer_case_mailer`. This file is deliberately thin.

    Preview customer report     render everything, store a PREVIEW note
    Send customer report        send (requires a matching preview) + note
    Test send customer report   send to the test recipients only + note

The hooks are registered SYNCHRONOUSLY (``run_asynchronously=False``).
Only then does the handler run inside the analyst's web request, which
IRIS needs for two things: the analyst identity (IRIS does not pass the
triggering user to asynchronous hook handlers) and the IRIS reporter
(it reads ``current_user`` while generating a report).

IRIS answers a manual hook in the UI only with "Queued task" - the
result of every preview and send attempt is therefore written into the
case as a note in the 'Communication' directory.
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
from iris_customer_case_mailer_module.customer_case_mailer.hook_actions import (
    MANUAL_HOOKS,
    report_setup_failure,
    run_hook,
)
from iris_customer_case_mailer_module.customer_case_mailer.iris_adapter import IrisAdapter
from iris_customer_case_mailer_module.customer_case_mailer.mailer import CustomerCaseMailer


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

    # ------------------------------------------------------------------- hooks

    def register_hooks(self, module_id: int):
        """Registers the manual case hooks (synchronous, see module doc)."""
        self.module_id = module_id
        for hook_ui_name in MANUAL_HOOKS:
            status = self.register_to_hook(
                module_id,
                iris_hook_name="on_manual_trigger_case",
                manual_hook_name=hook_ui_name,
                run_asynchronously=False,
            )
            if status.is_failure():
                self.log.error(status.get_message())
                self.log.error(status.get_data())
                return status
            self.log.info(f"Hook '{hook_ui_name}' registered.")
        return InterfaceStatus.I2Success("Hooks registered.")

    def hooks_handler(self, hook_name: str, hook_ui_name: str, data):
        """Runs the requested action for each case the hook was called on."""
        self.log.info(f"Hook '{hook_name}' ({hook_ui_name}) received.")
        messages = []
        case_ids = [getattr(case, "case_id", None) for case in data]
        case_ids = [cid for cid in case_ids if cid is not None]
        adapter = IrisAdapter()
        analyst_id, analyst_display = adapter.current_analyst()
        raw_config = {}
        try:
            raw_config = self._raw_module_config()
            mailer = CustomerCaseMailer(raw_config, adapter=adapter, logger=get_logger())
            for case_id in case_ids:
                result = run_hook(mailer, hook_ui_name, case_id,
                                  analyst_id, analyst_display)
                messages.append(f"Case #{case_id}: {result.message}")
        except MailerError as exc:
            # Invalid configuration is detected before any case is touched.
            # Write it into the cases anyway - IRIS only shows "Queued task".
            self.log.error(exc.user_message)
            for case_id in case_ids:
                report_setup_failure(adapter, raw_config, hook_ui_name, case_id,
                                     analyst_id, analyst_display, exc.user_message)
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
        """Current module configuration as a flat dict (fresh from IRIS)."""
        conf = self.module_dict_conf
        if conf:
            return raw_config_from_iris(conf)
        return raw_config_from_iris(IrisAdapter().get_module_raw_config())
