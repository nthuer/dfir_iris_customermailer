"""IRIS processor module: customer_case_mailer.

IRIS discovers a module's interface through ``__iris_module_interface``:
``instantiate_module_from_name()`` imports the package, reads this
attribute, then imports ``<package>.<__iris_module_interface>`` and
instantiates the class of the same name from that submodule.

The value below therefore has to match BOTH the file name
``IrisCustomerCaseMailerInterface.py`` and the class it contains.

Note: the interface class is deliberately NOT imported here. IRIS
imports the submodule itself, and keeping this file free of
``iris_interface`` imports lets the domain package
(``customer_case_mailer``) be imported and unit-tested without IRIS.
"""

__version__ = "1.1.0"
__iris_module_interface = "IrisCustomerCaseMailerInterface"
