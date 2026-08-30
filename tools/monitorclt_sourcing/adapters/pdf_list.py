"""Generic PDF-list adapter.

Some counties publish a signal only as a PDF — most importantly the **statutory
delinquent-tax advertisement** every NC county must post annually (owner +
property + amount due). This adapter downloads the PDF, extracts a layout-
preserving text rendering with `pdftotext -layout`, and parses each row with a
registry-supplied regex (named groups become row fields). Rows then flow through
base.normalize_row like any other source.

Two knobs in the registry entry:
  - request.url / request.headers: the PDF URL and headers. County sites are
    often behind Akamai/Cloudflare and 403 a bare request, so the default headers
    mimic a real browser (UA, Accept, Accept-Language, Sec-Fetch-*, Referer).
  - row_regex: a Python regex with named groups matched per text line. Lines that
    don't match (page headers, legal preamble) are skipped — the anti-fabrication
    default: only a line that parses into a real row becomes a signal.

Requires the `pdftotext` binary (poppler-utils), present in this environment.
"""

import os
import re
import subprocess
import tempfile
import urllib.request

from .base import SourceAdapter

_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
}


def default_fetch_text(entry):
    """Download the PDF and return layout-preserving text via pdftotext."""
    req = entry["request"]
    headers = dict(_BROWSER_HEADERS)
    headers.update(req.get("headers", {}))
    r = urllib.request.Request(req["url"], headers=headers)
    with urllib.request.urlopen(r, timeout=90) as resp:
        pdf_bytes = resp.read()
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            path = f.name
        out = subprocess.run(["pdftotext", "-layout", path, "-"],
                             capture_output=True, text=True, timeout=180)
        return out.stdout
    finally:
        if path and os.path.exists(path):
            os.unlink(path)


class PdfListAdapter(SourceAdapter):
    platform = "pdf"

    def __init__(self, fetch_json=None):
        # injected callable takes the entry and returns extracted text (tests pass
        # text directly; live path downloads + pdftotext).
        super().__init__(fetch_json or default_fetch_text)

    def fetch_rows(self, entry, zip_code=None):
        text = self._fetch_json(entry)
        regex = re.compile(entry["row_regex"])
        for line in text.splitlines():
            m = regex.match(line)
            if not m:
                continue
            row = m.groupdict()
            # a stable per-row key (the PDF has no record id) = the normalized line
            row["_rowline"] = " ".join(line.split())
            yield row
