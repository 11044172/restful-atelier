import re


SIGNED_PATH = re.compile(r"/(?:pay|shop/cancel)/[^\s?]+/?")


def redact_signed_paths(value):
    if not isinstance(value, str):
        return value
    return SIGNED_PATH.sub(lambda match: "/pay/<redacted>/" if match.group(0).startswith("/pay/") else "/shop/cancel/<redacted>/", value)


class SensitivePathFilter:
    def filter(self, record):
        record.msg = redact_signed_paths(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact_signed_paths(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: redact_signed_paths(value) for key, value in record.args.items()}
        return True
