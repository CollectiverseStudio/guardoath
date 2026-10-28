"""Explicit supported schema formats; unknown formats fail configuration."""

from email_validator import EmailNotValidError, validate_email
from jsonschema import FormatChecker

SUPPORTED_FORMATS = {"date", "date-time", "time", "ipv4", "ipv6", "uuid", "email"}
FORMAT_CHECKER = FormatChecker(formats=sorted(SUPPORTED_FORMATS))


@FORMAT_CHECKER.checks("email", raises=EmailNotValidError)
def email_format(value):
    if not isinstance(value, str):
        return True
    validate_email(value, check_deliverability=False)
    return True


def validate_schema_formats(schema):
    if not isinstance(schema, dict):
        return
    if "format" in schema and isinstance(schema["format"], str) and schema["format"] not in SUPPORTED_FORMATS:
        raise ValueError("Unsupported schema format")
    for name in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas"):
        for child in schema.get(name, {}).values():
            validate_schema_formats(child)
    for name in ("allOf", "anyOf", "oneOf", "prefixItems"):
        for child in schema.get(name, []):
            validate_schema_formats(child)
    for name in ("items", "contains", "additionalProperties", "unevaluatedProperties",
                 "unevaluatedItems", "propertyNames", "not", "if", "then", "else"):
        validate_schema_formats(schema.get(name))
