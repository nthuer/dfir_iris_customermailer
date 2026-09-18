from setuptools import find_packages, setup

# Distribution name follows the DFIR-IRIS module convention
# (dash-separated), while the importable package - and therefore the
# name registered in the IRIS module management - stays
# `iris_customer_case_mailer_module`.
setup(
    name="iris-customer-case-mailer-module",
    version="1.1.0",
    description=(
        "DFIR-IRIS processor module: sends customer-ready investigation "
        "reports by email directly from a case (preview, send and test-send hooks)."
    ),
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/nthuer/dfir_iris_customermailer",
    author="Niklas Thürnau",
    license="Apache-2.0",
    classifiers=[
        "License :: OSI Approved :: Apache Software License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
    ],
    python_requires=">=3.9",
    packages=find_packages(exclude=["tests", "tests.*"]),
    include_package_data=True,
    package_data={
        # Default HTML mail templates, so the module is usable right
        # after installation without copying files into the containers.
        "iris_customer_case_mailer_module.customer_case_mailer": [
            "mail_templates/*.html",
        ],
    },
    install_requires=[
        # Same template engine as the IRIS report templates (docxtpl/Jinja2).
        # Already present in IRIS, pinned here for standalone installs.
        "Jinja2>=3.1",
    ],
    extras_require={
        "dev": ["pytest>=7", "wheel"],
    },
)
