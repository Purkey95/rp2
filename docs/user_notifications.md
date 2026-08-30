<!--- Copyright 2026 eprbell --->
<!--- Licensed under the Apache License, Version 2.0 (the "License"); --->
<!--- you may not use this file except in compliance with the License. --->
<!--- You may obtain a copy of the License at --->
<!--- http://www.apache.org/licenses/LICENSE-2.0 --->

# RP2 Run Notifications (SendGrid Email / Twilio SMS)

RP2 can optionally send a notification when a tax report generation run completes or fails:

- **email** via [SendGrid](https://sendgrid.com)
- **SMS** via [Twilio](https://www.twilio.com)

Notifications are **opt-in**: they are only sent when the `--notify` command line flag is passed, e.g.:

```console
rp2_us --notify -o output -p rp2_ config.ini input.ods
```

Notifications are best-effort: a delivery failure (bad credentials, network down, etc.) is logged as a warning and never affects the tax computation or the process exit code.

Either service, or both, can be configured. Credentials are read from environment variables only — they are never stored in RP2 configuration files, so they cannot end up in version control.

## Email (SendGrid)

Prerequisites:
1. A SendGrid account with an API key that has the "Mail Send" permission (Settings > API Keys).
2. A verified sender identity (Settings > Sender Authentication): SendGrid rejects mail from unverified `from` addresses.

Environment variables (all three are required):

| Variable | Meaning | Example |
|---|---|---|
| `SENDGRID_API_KEY` | SendGrid API key | `SG.xxxxxxxx...` |
| `RP2_NOTIFICATION_EMAIL_FROM` | Verified sender address | `reports@example.com` |
| `RP2_NOTIFICATION_EMAIL_TO` | Recipient address(es), comma-separated | `me@example.com` |

## SMS (Twilio)

Prerequisites:
1. A Twilio account with an SMS-capable phone number.
2. The Account SID and Auth Token from the Twilio Console dashboard.
3. If the account is a trial account, destination numbers must be verified in the Twilio Console first.

Environment variables (all four are required):

| Variable | Meaning | Example |
|---|---|---|
| `TWILIO_ACCOUNT_SID` | Twilio Account SID | `ACxxxxxxxx...` |
| `TWILIO_AUTH_TOKEN` | Twilio Auth Token | `xxxxxxxx` |
| `TWILIO_FROM_NUMBER` | Twilio phone number (E.164 format) | `+15551234567` |
| `RP2_NOTIFICATION_SMS_TO` | Recipient number(s), E.164, comma-separated | `+15557654321` |

SMS bodies are truncated to 160 characters (one SMS segment).

## Example

```console
export SENDGRID_API_KEY="SG.your_key_here"
export RP2_NOTIFICATION_EMAIL_FROM="reports@example.com"
export RP2_NOTIFICATION_EMAIL_TO="me@example.com"

export TWILIO_ACCOUNT_SID="ACyour_sid_here"
export TWILIO_AUTH_TOKEN="your_token_here"
export TWILIO_FROM_NUMBER="+15551234567"
export RP2_NOTIFICATION_SMS_TO="+15557654321"

rp2_us --notify -o output -p rp2_ config.ini input.ods
```

On success the notification contains the list of processed assets and the output directory; on failure it points at the RP2 log file.

## Security Notes

- Treat `SENDGRID_API_KEY` and `TWILIO_AUTH_TOKEN` like passwords: never commit them to version control.
- Notification content is intentionally minimal (asset tickers and output directory only): no amounts, gains/losses or transaction data are ever transmitted.
