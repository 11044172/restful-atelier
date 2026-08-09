from django.conf import settings


def line_settings_configured():
    return all(
        (
            settings.LINE_LOGIN_CHANNEL_ID,
            settings.LINE_LOGIN_CHANNEL_SECRET,
            settings.LINE_LOGIN_CALLBACK_URL,
            settings.LINE_MESSAGING_CHANNEL_ACCESS_TOKEN,
            settings.LINE_MESSAGING_CHANNEL_SECRET,
            settings.LINE_OFFICIAL_ACCOUNT_BASIC_ID,
        )
    )
