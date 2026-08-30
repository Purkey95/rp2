# Copyright 2026 eprbell
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import os
from base64 import b64encode
from dataclasses import dataclass
from typing import List, Optional
from urllib import request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from rp2.logger import LOGGER

# SendGrid (email) environment variables.
SENDGRID_API_KEY_ENV: str = "SENDGRID_API_KEY"
NOTIFICATION_EMAIL_FROM_ENV: str = "RP2_NOTIFICATION_EMAIL_FROM"
NOTIFICATION_EMAIL_TO_ENV: str = "RP2_NOTIFICATION_EMAIL_TO"

# Twilio (SMS) environment variables.
TWILIO_ACCOUNT_SID_ENV: str = "TWILIO_ACCOUNT_SID"
TWILIO_AUTH_TOKEN_ENV: str = "TWILIO_AUTH_TOKEN"
TWILIO_FROM_NUMBER_ENV: str = "TWILIO_FROM_NUMBER"
NOTIFICATION_SMS_TO_ENV: str = "RP2_NOTIFICATION_SMS_TO"

_SENDGRID_API_URL: str = "https://api.sendgrid.com/v3/mail/send"
_TWILIO_API_URL_FORMAT: str = "https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
_HTTP_TIMEOUT_SECONDS: float = 15


@dataclass(frozen=True)
class EmailConfiguration:
    api_key: str
    from_address: str
    to_addresses: List[str]

    @classmethod
    def from_environment(cls) -> Optional["EmailConfiguration"]:
        api_key: str = os.environ.get(SENDGRID_API_KEY_ENV, "").strip()
        from_address: str = os.environ.get(NOTIFICATION_EMAIL_FROM_ENV, "").strip()
        to_addresses: List[str] = [address.strip() for address in os.environ.get(NOTIFICATION_EMAIL_TO_ENV, "").split(",") if address.strip()]
        if not api_key and not from_address and not to_addresses:
            return None
        if not api_key or not from_address or not to_addresses:
            # Literal environment variable names (not values) are logged here: keeping them out of the argument list
            # avoids a CodeQL clear-text-logging false positive on the *_API_KEY_ENV identifier.
            LOGGER.warning(
                "Email notification is partially configured and will be skipped: "
                "set all of SENDGRID_API_KEY, RP2_NOTIFICATION_EMAIL_FROM and RP2_NOTIFICATION_EMAIL_TO"
            )
            return None
        return cls(api_key=api_key, from_address=from_address, to_addresses=to_addresses)


@dataclass(frozen=True)
class SmsConfiguration:
    account_sid: str
    auth_token: str
    from_number: str
    to_numbers: List[str]

    @classmethod
    def from_environment(cls) -> Optional["SmsConfiguration"]:
        account_sid: str = os.environ.get(TWILIO_ACCOUNT_SID_ENV, "").strip()
        auth_token: str = os.environ.get(TWILIO_AUTH_TOKEN_ENV, "").strip()
        from_number: str = os.environ.get(TWILIO_FROM_NUMBER_ENV, "").strip()
        to_numbers: List[str] = [number.strip() for number in os.environ.get(NOTIFICATION_SMS_TO_ENV, "").split(",") if number.strip()]
        if not account_sid and not auth_token and not from_number and not to_numbers:
            return None
        if not account_sid or not auth_token or not from_number or not to_numbers:
            # Literal environment variable names (not values) are logged here: keeping them out of the argument list
            # avoids a CodeQL clear-text-logging false positive on the *_AUTH_TOKEN_ENV identifier.
            LOGGER.warning(
                "SMS notification is partially configured and will be skipped: "
                "set all of TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER and RP2_NOTIFICATION_SMS_TO"
            )
            return None
        return cls(account_sid=account_sid, auth_token=auth_token, from_number=from_number, to_numbers=to_numbers)


def send_notifications(subject: str, body: str) -> bool:
    # Notifications are best-effort: a delivery failure must never affect the outcome of a tax computation run,
    # so all errors are logged and swallowed.
    email_configuration: Optional[EmailConfiguration] = EmailConfiguration.from_environment()
    sms_configuration: Optional[SmsConfiguration] = SmsConfiguration.from_environment()

    if email_configuration is None and sms_configuration is None:
        LOGGER.warning(
            "Notifications were requested (--notify) but neither email (SendGrid) nor SMS (Twilio) is configured: "
            "see docs/user_notifications.md for setup instructions"
        )
        return False

    sent: bool = False
    if email_configuration is not None:
        sent = _send_email(email_configuration, subject, body) or sent
    if sms_configuration is not None:
        sent = _send_sms(sms_configuration, f"{subject}\n{body}") or sent
    return sent


def _send_email(configuration: EmailConfiguration, subject: str, body: str) -> bool:
    payload: bytes = json.dumps(
        {
            "personalizations": [{"to": [{"email": address} for address in configuration.to_addresses]}],
            "from": {"email": configuration.from_address},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body}],
        }
    ).encode("utf-8")
    email_request: request.Request = request.Request(
        _SENDGRID_API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {configuration.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    if _post(email_request, "SendGrid"):
        LOGGER.info("Email notification sent to %s", ", ".join(configuration.to_addresses))
        return True
    return False


def _send_sms(configuration: SmsConfiguration, body: str) -> bool:
    # Twilio SMS segments are 160 characters: keep the message to a single segment.
    truncated_body: str = body if len(body) <= 160 else f"{body[:157]}..."
    credentials: str = b64encode(f"{configuration.account_sid}:{configuration.auth_token}".encode("utf-8")).decode("ascii")
    result: bool = False
    for to_number in configuration.to_numbers:
        payload: bytes = urlencode({"From": configuration.from_number, "To": to_number, "Body": truncated_body}).encode("utf-8")
        sms_request: request.Request = request.Request(
            _TWILIO_API_URL_FORMAT.format(account_sid=configuration.account_sid),
            data=payload,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        if _post(sms_request, "Twilio"):
            LOGGER.info("SMS notification sent to %s", to_number)
            result = True
    return result


def _post(http_request: request.Request, service_name: str) -> bool:
    # Both notification endpoints are hardcoded https:// constants; guard anyway so urlopen can never receive another scheme.
    if not http_request.full_url.startswith("https://"):
        LOGGER.warning("%s notification skipped: URL is not HTTPS", service_name)
        return False
    try:
        with request.urlopen(http_request, timeout=_HTTP_TIMEOUT_SECONDS) as response:  # nosec B310
            status: int = response.status
            if 200 <= status < 300:
                return True
            LOGGER.warning("%s notification failed with HTTP status %s", service_name, status)
    except HTTPError as exception:
        LOGGER.warning("%s notification failed with HTTP status %s: %s", service_name, exception.code, exception.reason)
    except URLError as exception:
        LOGGER.warning("%s notification failed with network error: %s", service_name, exception.reason)
    except Exception as exception:  # pylint: disable=broad-except
        LOGGER.warning("%s notification failed with unexpected error: %s", service_name, exception)
    return False
