"""Minimal TwiML builders (the XML Twilio uses to control a call). Pure stdlib."""

from xml.sax.saxutils import escape, quoteattr


def _attrs(d):
    return "".join(f" {k}={quoteattr(str(v))}" for k, v in d.items() if v is not None)


def response(*children):
    body = "".join(children)
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'


def say(text, voice="Polly.Joanna"):
    return f"<Say{_attrs({'voice': voice})}>{escape(text)}</Say>"


def dial_number(number, caller_id=None, record=None, whisper_url=None,
                answer_on_bridge=True, timeout=20):
    n_attrs = {"url": whisper_url} if whisper_url else {}
    d_attrs = {"callerId": caller_id, "record": record, "timeout": timeout,
               "answerOnBridge": "true" if answer_on_bridge else None}
    return f"<Dial{_attrs(d_attrs)}><Number{_attrs(n_attrs)}>{escape(number)}</Number></Dial>"


def record(max_length=120, transcribe=False, action=None):
    return f"<Record{_attrs({'maxLength': max_length, 'transcribe': str(transcribe).lower(), 'action': action})}/>"


def reject(reason="rejected"):
    return f"<Reject{_attrs({'reason': reason})}/>"
