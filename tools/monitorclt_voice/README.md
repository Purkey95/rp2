# MonitorCLT Voice — inbound-call webhook with caller-ID screen pop

When a seller calls, this resolves their number to the parcel + **Owner Distress
Score** and hands the call to you already knowing who's on the line and how hot
the property is — the integration that makes any call option (AI agent or human)
worth far more. Speed-to-lead is the biggest conversion lever in acquisitions;
this makes the instant answer an *informed* one.

## Call flow

```
Seller calls your Twilio number
      │  Twilio POST /voice/inbound  (signed)
      ▼
 [signature gate]  ── invalid ──▶ 403
      │ valid
      ▼
 caller-ID → normalize → contact → parcel → Owner Distress Score
      │
      ├─▶ screen pop  → call_log.jsonl (+ optional push to dashboard/Slack/CRM)
      └─▶ TwiML: greet (+recording notice) → Dial operator
                       └─ whisper: "Incoming seller. CAROLINA HOLDINGS LLC,
                          123 N Main St. Score 98, immediate. Signals:
                          tax_delinquency, code_violation, absentee."
                       → warm-transfer, record, voicemail fallback
```

The operator hears the one-line distress read *before* the caller is bridged
(the whisper); a dashboard gets the full screen pop payload.

## Run (reference server, zero dependencies)

```bash
cp config.example.env .env    # fill in TWILIO_AUTH_TOKEN, OPERATOR_NUMBER, PUBLIC_BASE_URL
set -a; . ./.env; set +a
python3 server.py             # POST /voice/inbound  and  /voice/whisper
python3 test_voice.py         # 21 checks, offline
```

Point your Twilio number's **Voice → "A CALL COMES IN"** webhook at
`https://<PUBLIC_BASE_URL>/voice/inbound` (POST).

## Security & compliance (built in)

- **Twilio signature validation** gates every request (`twilio_sig.py`) — HMAC-SHA1
  over the URL + params against `X-Twilio-Signature`. No valid signature → 403, so
  nobody can spoof the webhook to pull a caller's distress profile. Auth token
  comes from the environment; nothing secret is committed.
- **Recording consent**: `TWO_PARTY_CONSENT=true` prepends the recording notice
  (set it for two-party-consent states).
- Inbound calls carry implied consent for *this* call; capture explicit consent
  before any automated follow-up (ties into the SMS/TCPA track).

## Structure (transport-free core, thin runner)

- `inbound.py` — pure `handle_inbound` / `handle_whisper` → (TwiML, screen-pop).
  Drop these into Flask / FastAPI / Lambda unchanged.
- `lookup.py` — phone normalize + `CallerLookup` (contact → parcel → lead).
- `twiml.py` — minimal TwiML builders (XML-escaped).
- `twilio_sig.py` — signature validation.
- `server.py` — stdlib `http.server` runner (signature gate, screen-pop emit).

## Wiring to live data

Replace `load_contacts_csv` / `load_leads_jsonl` with Postgres queries: `contacts`
(phone → apn) joined to the score's `leads` table. That's the only change for
production — the call logic is unchanged.

## Adding an AI voice agent

This is the front door either way:
- **Human-first:** `Dial` the operator (current default) with the whisper.
- **AI-first:** point the `Dial`/`Connect` at your Vapi/Retell/Bland agent's SIP or
  number instead of the operator, and pass the screen pop (owner, score, signals)
  as context so the agent opens with the property already in hand; warm-transfer
  the hot ones to a human. Same webhook, same screen pop — you choose who answers.
