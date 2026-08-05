from setuptools import find_packages, setup

setup(
    name="iris_customer_case_mailer_module",
    version="1.0.0",
    description=(
        "DFIR-IRIS processor module: sends customer-ready investigation "
        "reports by email directly from a case (hook 'Send customer report')."
    ),
    author="Niklas Thürnau",
    license="Apache-2.0",
    classifiers=[
        "License :: OSI Approved :: Apache Software License",
        "Programming Language :: Python :: 3",
    ],
    python_requires=">=3.9",
    packages=find_packages(exclude=["tests", "tests.*"]),
    include_package_data=True,
    package_data={
        "iris_customer_case_mailer_module.customer_case_mailer.ui": [
            "templates/*.html",
        ],
    },
    install_requires=[
        # Same template engine as the IRIS report templates (docxtpl/Jinja2).
        "Jinja2>=3.1",
    ],
    extras_require={
        "dev": ["pytest>=7"],
    },
)
