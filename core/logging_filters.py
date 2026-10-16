import logging
import re

# All bearer-token URL prefixes belong here, shared by middleware and formatting.
SIGNED_PATH = re.compile(r"(/(?:pay|shop/cancel|payments/ecpay/return)/)[^\s?\"'<>]+")


def redact_signed_paths(value):
    if not isinstance(value, str):
        return value
    return SIGNED_PATH.sub(lambda match: match.group(1) + "<redacted>/", value)


class SensitivePathFilter:
    def filter(self, record):
        record.msg = redact_signed_paths(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact_signed_paths(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: redact_signed_paths(value) for key, value in record.args.items()}
        return True


class SensitivePathFormatter(logging.Formatter):
    def format(self, record):
        # Also sanitize exception tracebacks, which are formatted after filters run.
        return redact_signed_paths(super().format(record))
