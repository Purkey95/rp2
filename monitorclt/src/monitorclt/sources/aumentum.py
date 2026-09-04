"""Register of Deeds index on Aumentum Recorder (Harris Recording Solutions) public sites.

Mecklenburg's meckrod.manatron.com is one; several other North Carolina registers run
the same "ROD Web Access" application. It is an ASP.NET WebForms app with Infragistics
controls, so the conversation is: accept the disclaimer (postback), load the search
form, post a date-range search with document types, then page through
SearchResults.aspx twenty rows at a time (a plain GET with ?pg=N; the search itself
lives in the server session). Every field the browser sends was recorded
from a real session; this module replays it with the standard library.

The results grid carries, per instrument: number, book/page, date filed, document
type, first grantor and first grantee (with a "(+)" marker when there are more),
person/company flags, and the legal description, which in Mecklenburg usually ends
with "PIN/PLSLIDE <parcel id>" -- the link from a deed to a parcel.

Manners: one request at a time, a pause between requests, narrow date windows, an
honest user agent. The site's disclaimer places no restriction on automated use; keep
it that way by not being a burden.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .base import Connector, Fetched
from .html_tables import parse_tables
from .transport import Transport

TXT_STATE = '|0|01||[[[[]],[],[]],[{},[]],"01"]'
TEXT_FIELDS = (
    "cphNoMargin_f_txtInstrumentNoFrom",
    "cphNoMargin_f_txtInstrumentNoTo",
    "cphNoMargin_f_txtBook",
    "cphNoMargin_f_txtPage",
    "cphNoMargin_f_DataTextEdit1",
    "cphNoMargin_f_txtLDLot",
    "cphNoMargin_f_txtLDBook",
    "cphNoMargin_f_txtUnit",
    "cphNoMargin_f_txtLDFreeForm",
)
# Instrument types worth watching for an estate/distress workflow. Codes are the site's own.
DEFAULT_DOC_TYPES = ("DEED", "EXTR EST", "QCD", "TR/D", "C/D", "COM/D", "SHF/D", "FORECLOS", "NOTC FOR", "SUB TR", "LIS/P", "EST TAX")
PIN_RE = re.compile(r"PIN/PLSLIDE\s+([0-9A-Z\-]+)", re.I)


def date_state(day: dt.date) -> str:
    v = "01{0}-{1}-{2}-0-0-0-0".format(day.year, day.month, day.day)
    return '|0|{0}||[[[[]],[],[]],[{{}},[]],"{0}"]'.format(v)


def hidden_fields(html: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for m in re.finditer(r"<input([^>]*)>", html):
        attrs = m.group(1)
        name = re.search(r'name="([^"]+)"', attrs)
        typ = re.search(r'type="([^"]+)"', attrs)
        if name and typ and typ.group(1).lower() == "hidden":
            val = re.search(r'value="([^"]*)"', attrs)
            out[name.group(1)] = val.group(1) if val else ""
    return out


def doc_type_fields(html: str) -> Dict[str, str]:
    """Checkbox field name for each document-type code on the search form (index varies per site)."""
    out: Dict[str, str] = {}
    for m in re.finditer(r'<input[^>]*name="(ctl00\$cphNoMargin\$f\$dclDocType\$\d+)"[^>]*value="([^"]+)"', html):
        out[m.group(2)] = m.group(1)
    return out


def records_found(html: str) -> Optional[int]:
    m = re.search(r'id="[^"]*_TotalRows"[^>]*>\s*(\d+)\s*<', html) or re.search(r"\(\s*(\d+)\s+records found", html)
    return int(m.group(1)) if m else None


def showing(html: str) -> Optional[Tuple[int, int]]:
    a = re.search(r'id="[^"]*_StartRow"[^>]*>\s*(\d+)\s*<', html)
    b = re.search(r'id="[^"]*_EndRow"[^>]*>\s*(\d+)\s*<', html)
    if a and b:
        return int(a.group(1)), int(b.group(1))
    m = re.search(r"Showing Records (\d+) through (\d+)", html)
    return (int(m.group(1)), int(m.group(2))) if m else None


_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def grid_rows(html: str) -> List[List[str]]:
    """Cell texts per row of the Infragistics data table (the one marked mkr:dataTbl)."""
    start = html.find("mkr:dataTbl")
    if start < 0:
        return []
    start = html.rfind("<table", 0, start)
    end = html.find("</table>", start)
    rows = []
    for row in _ROW_RE.findall(html[start:end]):
        cells = [re.sub(r"\s+", " ", _TAG_RE.sub(" ", c)).replace("&nbsp;", " ").strip() for c in _CELL_RE.findall(row)]
        if cells:
            rows.append(cells)
    return rows


class AumentumDeedConnector(Connector):
    """`spec`, `base_url`, `county` come from the county plugin; `doc_types` narrows the pull."""

    spec: Any
    base_url: str = ""
    doc_types: Tuple[str, ...] = DEFAULT_DOC_TYPES
    window_days: int = 7  # incremental window when no watermark
    backfill_from: Optional[str] = None  # ISO date for a first pull
    max_pages: int = 500

    def __init__(self, county: str, endpoint: Optional[str] = None, today: Optional[dt.date] = None) -> None:
        super().__init__(county)
        self.endpoint = endpoint or self.base_url
        self.today = today or dt.date.today()

    # ------------------------------------------------------------- fetch ---

    def date_range(self, watermark: Optional[str]) -> Tuple[dt.date, dt.date]:
        end = self.today
        if watermark:
            start = dt.date.fromisoformat(str(watermark)[:10]) - dt.timedelta(days=1)  # overlap a day; idempotent anyway
        elif self.backfill_from:
            start = dt.date.fromisoformat(self.backfill_from)
        else:
            start = end - dt.timedelta(days=self.window_days)
        return start, end

    def fetch(self, transport: Transport, watermark: Optional[str]) -> Iterable[Fetched]:
        if self.endpoint.startswith("fixture://"):
            page = 0
            while page < self.max_pages:
                url = "{0}/{1}.html".format(self.endpoint.rstrip("/"), page)
                try:
                    body, ctype = transport.get(url)
                except FileNotFoundError:
                    return
                yield Fetched(body=body, url=url, content_type=ctype or "text/html", watermark=self.today.isoformat())
                page += 1
            return

        base = self.endpoint.rstrip("/")
        start, end = self.date_range(watermark)
        home, _ = transport.get(base + "/")
        f = hidden_fields(home.decode("utf-8", errors="replace"))
        f.update(
            {
                "__EVENTTARGET": "ctl00$cph1$lnkAccept",
                "__EVENTARGUMENT": "",
                "ctl00$LoginForm1$logonType": "rdoPubCpu",
                "LoginForm1_txtLogonName": "",
                "LoginForm1_txtPassword": "",
            }
        )
        transport.post(base + "/", f, base + "/")
        entry, _ = transport.get(base + "/RealEstate/SearchEntry.aspx")
        entry_html = entry.decode("utf-8", errors="replace")
        f = hidden_fields(entry_html)
        f.update(
            {
                "__EVENTTARGET": "ctl00$cphNoMargin$SearchButtons1$btnSearch",
                "__EVENTARGUMENT": "0",
                "ctl00$cphNoMargin$f$NameSearchMode": "rdoCombine",
                "cphNoMargin_f_txtParty": "Lastname Firstname",
                "cphNoMargin_f_txtParty_clientState": TXT_STATE,
                "ctl00$cphNoMargin$f$chkEnableNameSuggest": "on",
                "ctl00$cphNoMargin$f$ddlGrantorRole": "",
                "ctl00$cphNoMargin$f$ddlSearchType": "",
                "ctl00$cphNoMargin$f$ddlGrantorType": "",
                "ctl00$cphNoMargin$f$drbPartyType": "",
                "cphNoMargin_f_ddcDateFiledFrom_clientState": date_state(start),
                "cphNoMargin_f_ddcDateFiledTo_clientState": date_state(end),
                "ctl00$LoginForm1$logonType": "rdoPubCpu",
                "LoginForm1_txtLogonName": "",
                "LoginForm1_txtPassword": "",
                "ctl00$cphNoMargin$SearchButtons1$btnSearch__10": ":0",
            }
        )
        for k in TEXT_FIELDS:
            f.setdefault(k, "")
            f.setdefault(k + "_clientState", TXT_STATE)
        available = doc_type_fields(entry_html)
        for code in self.doc_types:
            if code in available:
                f[available[code]] = code
        results, _ = transport.post(base + "/RealEstate/SearchEntry.aspx", f, base + "/RealEstate/SearchEntry.aspx")
        html = results.decode("utf-8", errors="replace")
        if "SearchResults" not in html and showing(html) is None:
            results, _ = transport.get(base + "/RealEstate/SearchResults.aspx")
            html = results.decode("utf-8", errors="replace")
        total = records_found(html)
        page = 1
        while True:
            yield Fetched(
                body=html.encode("utf-8"),
                url="{0}/RealEstate/SearchResults.aspx#page{1}".format(base, page),
                content_type="text/html",
                watermark=end.isoformat(),
            )
            span = showing(html)
            if total is None or span is None or span[1] >= total or page >= self.max_pages:
                return
            page += 1
            results, _ = transport.get("{0}/RealEstate/SearchResults.aspx?pg={1}".format(base, page))
            html = results.decode("utf-8", errors="replace")

    # ------------------------------------------------------------- parse ---

    def parse(self, body: bytes, content_type: Optional[str] = None) -> List[Dict[str, Any]]:
        html = body.decode("utf-8", errors="replace")
        rows = grid_rows(html)
        if not rows:  # a browser-rendered save has real <th> headers; fall back to the generic table parser
            for table in parse_tables(html):
                if table and "Date Filed" in table[0]:
                    rows = [list(r.values()) for r in table]
                    break
        out = []
        for cells in rows:
            rec = self.to_record(cells)
            if rec:
                out.append(rec)
        return out

    def to_record(self, cells: List[str]) -> Optional[Dict[str, Any]]:
        """Columns are located by what they contain, not where they sit: hidden grid
        columns come and go between sites and versions, the content does not."""
        import html as _html

        cells = [_html.unescape(c) for c in cells]
        combo = next((c for c in cells if re.fullmatch(r"\d{6,}\s+\d+-\s*\d+", c)), None)
        inst = book = page = None
        if combo:
            m = re.fullmatch(r"(\d{6,})\s+(\d+)-\s*(\d+)", combo)
            inst, book, page = m.group(1), m.group(2), m.group(3)  # type: ignore[union-attr]
        else:
            inst = next((c for c in cells if re.fullmatch(r"\d{8,}", c)), None)
        if not inst:
            return None
        date_idx = next((i for i, c in enumerate(cells) if re.fullmatch(r"\d{2}/\d{2}/\d{4}", c)), None)
        recorded = None
        doc_type = None
        if date_idx is not None:
            d = cells[date_idx]
            recorded = "{0}-{1}-{2}".format(d[6:], d[0:2], d[3:5])
            doc_type = next((c for c in cells[date_idx + 1 :] if c and not c.startswith("[")), None)
        names = next((c for c in cells if c.startswith("[R]") or c.startswith("[E]")), "")
        grantor = grantee = None
        m = re.match(r"\[R\]\s*(.*?)\s*(?:\[E\]\s*(.*))?$", names)
        if m:
            grantor, grantee = _clean_party(m.group(1)), _clean_party(m.group(2))
        legal = next((c for c in cells if "PIN/PLSLIDE" in c.upper() or re.search(r"\b(LT|LOT|BLK|BLOCK|SUB|UNIT|ACRES?|TRACT)\b", c)), None)
        pin = PIN_RE.search(legal or "")
        flags = [c for c in cells[-4:] if c in ("P", "C")]
        counts = [c for c in cells if re.fullmatch(r"\d{1,3}", c)]
        return {
            "county": self.county,
            "instrument_number": inst,
            "book": book,
            "page": page,
            "recorded_date": recorded,
            "instrument_type": doc_type,
            "grantor_name": grantor,
            "grantee_name": grantee,
            "grantor_is_org": bool(flags) and flags[0] == "C",
            "grantee_is_org": len(flags) > 1 and flags[-1] == "C",
            "more_grantors": "(+)" in (m.group(1) if m else ""),
            "more_grantees": "(+)" in ((m.group(2) or "") if m else ""),
            "parcel_pin": pin.group(1) if pin else None,
            "legal_description": legal,
            "document_id": next((c for c in cells if c.startswith("OPR")), None),
            "row_number": counts[0] if counts else None,
        }


def _clean_party(value: Optional[str]) -> Optional[str]:
    v = (value or "").replace("(+)", "").strip().strip(",").strip()
    return v or None


def _int(value: Optional[str]) -> int:
    try:
        return int((value or "0").strip() or 0)
    except ValueError:
        return 0
