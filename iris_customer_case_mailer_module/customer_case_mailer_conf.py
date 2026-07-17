"""Module metadata and configuration definition for the IRIS module management.

The parameters appear in IRIS under Advanced -> Modules ->
IrisCustomerCaseMailer -> Configuration. After configuration changes a
restart of the IRIS services may be required.
"""

module_name = "IrisCustomerCaseMailer"
module_description = (
    "Sends customer-ready investigation reports by email directly from a "
    "case (processor module, manual hook 'Send customer report')."
)
interface_version = "1.2.0"   # iris-module-interface, compatible with IRIS >= 2.4.27
module_version = "1.0.0"

pipeline_support = False
pipeline_info = {}

module_configuration = [
    {
        "param_name": "smtp_host",
        "param_human_name": "SMTP host",
        "param_description": "Hostname or IP of the SMTP server.",
        "default": None,
        "mandatory": True,
        "type": "string",
    },
    {
        "param_name": "smtp_port",
        "param_human_name": "SMTP port",
        "param_description": "SMTP port (587 = STARTTLS, 465 = implicit TLS, 25 = plaintext).",
        "default": 587,
        "mandatory": True,
        "type": "int",
    },
    {
        "param_name": "smtp_use_tls",
        "param_human_name": "Enable TLS",
        "param_description": "Use STARTTLS (port 465 always uses implicit TLS).",
        "default": True,
        "mandatory": True,
        "type": "bool",
    },
    {
        "param_name": "smtp_username",
        "param_human_name": "SMTP username",
        "param_description": "Optional. Leave empty to send without authentication.",
        "default": None,
        "mandatory": False,
        "type": "string",
    },
    {
        "param_name": "smtp_password",
        "param_human_name": "SMTP password",
        "param_description": "Optional. Never logged and never written into notes.",
        "default": None,
        "mandatory": False,
        "type": "sensitive_string",
    },
    {
        "param_name": "smtp_from_address",
        "param_human_name": "Sender address",
        "param_description": "From address of the customer mails.",
        "default": None,
        "mandatory": True,
        "type": "string",
    },
    {
        "param_name": "smtp_from_name",
        "param_human_name": "Sender name",
        "param_description": "Optional display name of the sender.",
        "default": None,
        "mandatory": False,
        "type": "string",
    },
    {
        "param_name": "smtp_timeout_seconds",
        "param_human_name": "SMTP timeout (seconds)",
        "param_description": "Timeout for connecting and sending.",
        "default": 30,
        "mandatory": False,
        "type": "int",
    },
    {
        "param_name": "default_cc",
        "param_human_name": "Fixed CC recipients (CSV)",
        "param_description": "Comma-separated CC addresses. Visible in the dialog, not editable.",
        "default": None,
        "mandatory": False,
        "type": "string",
    },
    {
        "param_name": "default_bcc",
        "param_human_name": "Fixed BCC recipients (CSV)",
        "param_description": "Comma-separated BCC addresses. Visible in the dialog, not editable.",
        "default": None,
        "mandatory": False,
        "type": "string",
    },
    {
        "param_name": "test_mode_enabled",
        "param_human_name": "Test mode",
        "param_description": ("If enabled, mail is NEVER sent to production recipients, "
                              "only to the test recipients."),
        "default": False,
        "mandatory": True,
        "type": "bool",
    },
    {
        "param_name": "test_mode_recipients",
        "param_human_name": "Test recipients (CSV)",
        "param_description": "Recipients in test mode/test send. Mandatory when test mode is on.",
        "default": None,
        "mandatory": False,
        "type": "string",
    },
    {
        "param_name": "customer_email_attribute",
        "param_human_name": "Customer attribute for recipients",
        "param_description": "Name of the customer custom attribute holding the To addresses (CSV).",
        "default": "contact_emails",
        "mandatory": True,
        "type": "string",
    },
    {
        "param_name": "notes_directory_name",
        "param_human_name": "Notes directory",
        "param_description": "Directory under which send notes are stored.",
        "default": "Communication",
        "mandatory": True,
        "type": "string",
    },
    {
        "param_name": "default_subject_template",
        "param_human_name": "Subject template",
        "param_description": ("Jinja2 template for the prefilled subject, e.g. "
                              "'Investigation Report – {{ case.name }} ({{ case.soc_id }})'."),
        "default": "Investigation Report – {{ case.name }} ({{ case.soc_id }})",
        "mandatory": True,
        "type": "string",
    },
    {
        "param_name": "allowed_report_formats",
        "param_human_name": "Allowed report formats",
        "param_description": "CSV of 'docx' and/or 'html'.",
        "default": "docx,html",
        "mandatory": True,
        "type": "string",
    },
    {
        "param_name": "allowed_report_templates",
        "param_human_name": "Allowed report templates (CSV)",
        "param_description": "Optional: names/ids of allowed investigation templates. Empty = all.",
        "default": None,
        "mandatory": False,
        "type": "string",
    },
    {
        "param_name": "allowed_mail_templates",
        "param_human_name": "Allowed mail templates (CSV)",
        "param_description": "Optional: file names of allowed HTML mail templates. Empty = all.",
        "default": None,
        "mandatory": False,
        "type": "string",
    },
    {
        "param_name": "mail_templates_dir",
        "param_human_name": "Mail template directory",
        "param_description": ("Server path containing the HTML mail templates (Jinja2, same "
                              "syntax as the IRIS report templates)."),
        "default": "/opt/iris/customer_case_mailer/mail_templates",
        "mandatory": True,
        "type": "string",
    },
    {
        "param_name": "default_mail_template",
        "param_human_name": "Default mail template",
        "param_description": "Preselection in the dialog and template for the hook direct send.",
        "default": "standard_customer_mail.html",
        "mandatory": False,
        "type": "string",
    },
    {
        "param_name": "default_report_template",
        "param_human_name": "Default report template",
        "param_description": "Preselection (name or id) for dialog and hook direct send.",
        "default": None,
        "mandatory": False,
        "type": "string",
    },
    {
        "param_name": "default_report_format",
        "param_human_name": "Default report format",
        "param_description": "'docx' or 'html'. Must be part of the allowed formats.",
        "default": "docx",
        "mandatory": False,
        "type": "string",
    },
    {
        "param_name": "manual_hook_sends_with_defaults",
        "param_human_name": "Hook sends directly with defaults",
        "param_description": ("If enabled, the manual hook sends immediately using the "
                              "default templates (no dialog). Otherwise the hook points "
                              "to the send dialog."),
        "default": False,
        "mandatory": False,
        "type": "bool",
    },
]
