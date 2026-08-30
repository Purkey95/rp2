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
import unittest
from typing import Any, Dict, Optional
from unittest import mock
from urllib.error import URLError
from urllib.parse import parse_qs

from rp2.notifier import (
    NOTIFICATION_EMAIL_FROM_ENV,
    NOTIFICATION_EMAIL_TO_ENV,
    NOTIFICATION_SMS_TO_ENV,
    SENDGRID_API_KEY_ENV,
    TWILIO_ACCOUNT_SID_ENV,
    TWILIO_AUTH_TOKEN_ENV,
    TWILIO_FROM_NUMBER_ENV,
    EmailConfiguration,
    SmsConfiguration,
    send_notifications,
)

_EMAIL_ENVIRONMENT: Dict[str, str] = {
    SENDGRID_API_KEY_ENV: "SG.test_key",
    NOTIFICATION_EMAIL_FROM_ENV: "reports@example.com",
    NOTIFICATION_EMAIL_TO_ENV: "me@example.com",
}

_SMS_ENVIRONMENT: Dict[str, str] = {
    TWILIO_ACCOUNT_SID_ENV: "ACtest",
    TWILIO_AUTH_TOKEN_ENV: "token",
    TWILIO_FROM_NUMBER_ENV: "+15550001111",
    NOTIFICATION_SMS_TO_ENV: "+15552223333",
}


def _mock_response(status: int = 202) -> mock.MagicMock:
    response: mock.MagicMock = mock.MagicMock()
    response.status = status
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class TestNotifier(unittest.TestCase):
    def setUp(self) -> None:
        self.maxDiff = None  # pylint: disable=invalid-name

    def test_email_configuration_absent(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(EmailConfiguration.from_environment())

    def test_email_configuration_partial(self) -> None:
        with mock.patch.dict("os.environ", {SENDGRID_API_KEY_ENV: "SG.test_key"}, clear=True):
            self.assertIsNone(EmailConfiguration.from_environment())

    def test_email_configuration_complete(self) -> None:
        environment: Dict[str, str] = dict(_EMAIL_ENVIRONMENT)
        environment[NOTIFICATION_EMAIL_TO_ENV] = "me@example.com, you@example.com"
        with mock.patch.dict("os.environ", environment, clear=True):
            configuration: Optional[EmailConfiguration] = EmailConfiguration.from_environment()
        self.assertIsNotNone(configuration)
        assert configuration is not None
        self.assertEqual(configuration.api_key, "SG.test_key")
        self.assertEqual(configuration.from_address, "reports@example.com")
        self.assertEqual(configuration.to_addresses, ["me@example.com", "you@example.com"])

    def test_sms_configuration_absent(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(SmsConfiguration.from_environment())

    def test_sms_configuration_partial(self) -> None:
        with mock.patch.dict("os.environ", {TWILIO_ACCOUNT_SID_ENV: "ACtest"}, clear=True):
            self.assertIsNone(SmsConfiguration.from_environment())

    def test_sms_configuration_complete(self) -> None:
        with mock.patch.dict("os.environ", _SMS_ENVIRONMENT, clear=True):
            configuration: Optional[SmsConfiguration] = SmsConfiguration.from_environment()
        self.assertIsNotNone(configuration)
        assert configuration is not None
        self.assertEqual(configuration.account_sid, "ACtest")
        self.assertEqual(configuration.to_numbers, ["+15552223333"])

    def test_send_notifications_unconfigured(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch("rp2.notifier.request.urlopen") as urlopen:
                self.assertFalse(send_notifications("subject", "body"))
        urlopen.assert_not_called()

    def test_send_email_notification(self) -> None:
        with mock.patch.dict("os.environ", _EMAIL_ENVIRONMENT, clear=True):
            with mock.patch("rp2.notifier.request.urlopen", return_value=_mock_response(202)) as urlopen:
                self.assertTrue(send_notifications("RP2 run completed", "All done"))
        urlopen.assert_called_once()
        http_request: Any = urlopen.call_args[0][0]
        self.assertEqual(http_request.full_url, "https://api.sendgrid.com/v3/mail/send")
        self.assertEqual(http_request.get_header("Authorization"), "Bearer SG.test_key")
        payload: Dict[str, Any] = json.loads(http_request.data.decode("utf-8"))
        self.assertEqual(payload["subject"], "RP2 run completed")
        self.assertEqual(payload["from"]["email"], "reports@example.com")
        self.assertEqual(payload["personalizations"][0]["to"], [{"email": "me@example.com"}])
        self.assertEqual(payload["content"], [{"type": "text/plain", "value": "All done"}])

    def test_send_sms_notification(self) -> None:
        with mock.patch.dict("os.environ", _SMS_ENVIRONMENT, clear=True):
            with mock.patch("rp2.notifier.request.urlopen", return_value=_mock_response(201)) as urlopen:
                self.assertTrue(send_notifications("RP2 run completed", "All done"))
        urlopen.assert_called_once()
        http_request: Any = urlopen.call_args[0][0]
        self.assertEqual(http_request.full_url, "https://api.twilio.com/2010-04-01/Accounts/ACtest/Messages.json")
        self.assertTrue(http_request.get_header("Authorization").startswith("Basic "))
        payload: Dict[str, Any] = parse_qs(http_request.data.decode("utf-8"))
        self.assertEqual(payload["From"], ["+15550001111"])
        self.assertEqual(payload["To"], ["+15552223333"])
        self.assertEqual(payload["Body"], ["RP2 run completed\nAll done"])

    def test_send_sms_notification_truncates_long_body(self) -> None:
        with mock.patch.dict("os.environ", _SMS_ENVIRONMENT, clear=True):
            with mock.patch("rp2.notifier.request.urlopen", return_value=_mock_response(201)) as urlopen:
                self.assertTrue(send_notifications("subject", "x" * 300))
        payload: Dict[str, Any] = parse_qs(urlopen.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(len(payload["Body"][0]), 160)
        self.assertTrue(payload["Body"][0].endswith("..."))

    def test_send_notifications_both_services(self) -> None:
        environment: Dict[str, str] = {**_EMAIL_ENVIRONMENT, **_SMS_ENVIRONMENT}
        with mock.patch.dict("os.environ", environment, clear=True):
            with mock.patch("rp2.notifier.request.urlopen", return_value=_mock_response(202)) as urlopen:
                self.assertTrue(send_notifications("subject", "body"))
        self.assertEqual(urlopen.call_count, 2)

    def test_network_error_is_swallowed(self) -> None:
        with mock.patch.dict("os.environ", _EMAIL_ENVIRONMENT, clear=True):
            with mock.patch("rp2.notifier.request.urlopen", side_effect=URLError("connection refused")):
                self.assertFalse(send_notifications("subject", "body"))


if __name__ == "__main__":
    unittest.main()
