"""Logging filters that prevent credentials and unnecessary identifiers leaking."""

import logging
import re


_SECRET_PATTERN = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|private[_-]?key|consumer[_-]?key|passkey|cvv|card[_-]?number)"
    r"(\s*[=:]\s*)([^,\s;]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)


def redact_log_value(value):
    value = _SECRET_PATTERN.sub(r"\1\2[REDACTED]", str(value))
    value = _BEARER_PATTERN.sub("Bearer [REDACTED]", value)
    return _EMAIL_PATTERN.sub("[EMAIL]", value)


class SensitiveDataRedactionFilter(logging.Filter):
    def filter(self, record):
        record.msg = redact_log_value(record.getMessage())
        record.args = ()
        return True
