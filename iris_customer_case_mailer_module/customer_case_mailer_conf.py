"""Module metadata and configuration definition for the IRIS module management.

The parameters appear in IRIS under Advanced -> Modules ->
IrisCustomerCaseMailer -> Configuration. After configuration changes a
restart of the IRIS services may be required.
"""

module_name = "IrisCustomerCaseMailer"
module_description = (
    "Sends customer-ready investigation reports by email directly from a "
    "case. Manual case hooks: 'Preview customer report', 'Send customer "
    "report', 'Test send customer report'."
)
interface_version = "1.2.0"   # iris-module-interface, compatible with IRIS >= 2.4.27
module_version = "1.1.0"

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
        "param_description": "Comma-separated CC addresses added to every customer mail.",
        "default": None,
        "mandatory": False,
        "type": "string",
    },
    {
        "param_name": "default_bcc",
        "param_human_name": "Fixed BCC recipients (CSV)",
        "param_description": "Comma-separated BCC addresses added to every customer mail.",
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
        "param_name": "customer_contact_roles",
        "param_human_name": "Customer contact roles (CSV)",
        "param_description": ("Contact roles that receive the report. Matched against the "
                              "'Contact Role' field of the customer contacts, "
                              "case-insensitive and exact. Default: CISO."),
        "default": "CISO",
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
        "param_description": ("Optional server path with your own HTML mail templates "
                              "(Jinja2, same syntax as the IRIS report templates). Leave "
                              "empty to use the templates shipped with the module. To use "
                              "your own, mount a directory into the app and worker "
                              "containers and enter its path here."),
        "default": None,
        "mandatory": False,
        "type": "string",
    },
    {
        "param_name": "mail_templates_html",
        "param_human_name": "Mail templates (editor)",
        "param_description": ("HTML mail templates, editable right here - IRIS shows this "
                              "parameter as a code editor, so no directory has to be "
                              "mounted. Separate several templates with marker lines: "
                              "<!-- template: name -->. A text without any marker counts "
                              "as one template named 'default'. Same Jinja2 syntax as the "
                              "IRIS report templates. Templates defined here are offered "
                              "in addition to the files in the template directory and win "
                              "on a name collision."),
        "default": None,
        "mandatory": False,
        "type": "textfield_html",
    },
    {
        "param_name": "default_mail_template",
        "param_human_name": "Default mail template",
        "param_description": ("Mail template used when the case does not set its own "
                              "(case custom attribute 'Mail template')."),
        "default": "standard_customer_mail.html",
        "mandatory": False,
        "type": "string",
    },
    {
        "param_name": "default_report_template",
        "param_human_name": "Default report template",
        "param_description": ("Investigation report template (name or id) used when the case "
                              "does not set its own (case custom attribute 'Report template'). "
                              "Set this or the case attribute - there is no implicit choice."),
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
        "param_name": "require_preview_before_send",
        "param_human_name": "Require preview before sending",
        "param_description": ("If enabled (recommended), 'Send customer report' only sends "
                              "when a preview note with exactly the same content exists in "
                              "the notes directory. Run 'Preview customer report' first. "
                              "Does not apply to test sends."),
        "default": True,
        "mandatory": False,
        "type": "bool",
    },
]
