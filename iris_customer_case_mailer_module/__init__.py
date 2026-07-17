"""IRIS processor module: customer_case_mailer.

IRIS expects the interface class at package level to register the
module. The import is defensive so the domain package
(customer_case_mailer) stays importable and testable even without the
iris-module-interface package installed.
"""

__version__ = "1.0.0"

try:
    from iris_customer_case_mailer_module.IrisCustomerCaseMailerInterface import (  # noqa: F401
        IrisCustomerCaseMailerInterface,
    )
except ImportError:  # outside of IRIS (e.g. unit tests)
    IrisCustomerCaseMailerInterface = None
