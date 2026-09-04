#!/usr/bin/env python3
"""Pseudonymize person names in captured live fixtures so the public repo carries the
real markup and record shapes without the real people.

Every name-bearing field (assessor owner columns, lien customer, deed parties, code
enforcement inspector) has its tokens replaced consistently: the same real surname
always becomes the same fake surname, so co-owners still share a name, estates still
read "ESTATE OF", trusts still read "... LIVING TRUST", initials stay initials, and
organizations keep their LLC/INC words. The mapping is derived from a secret salt and
is not committed. Addresses, parcel ids, values and dates are left as they are: they
are property facts, not people.

    python3 scripts/pseudonymize_fixtures.py fixtures-private/mecklenburg/live fixtures/mecklenburg/live
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys

KEEP = {
    "ESTATE",
    "OF",
    "THE",
    "HEIRS",
    "HEIR",
    "TRUST",
    "TRUSTEE",
    "TRUSTEES",
    "TTEE",
    "TTEES",
    "LIVING",
    "REVOCABLE",
    "IRREVOCABLE",
    "FAMILY",
    "AMENDED",
    "RESTATED",
    "AND",
    "DECLARATION",
    "AGREEMENT",
    "LLC",
    "L",
    "INC",
    "CORP",
    "CORPORATION",
    "CO",
    "COMPANY",
    "LP",
    "LLP",
    "PLLC",
    "LTD",
    "PARTNERS",
    "PARTNERSHIP",
    "HOLDINGS",
    "PROPERTIES",
    "PROPERTY",
    "REAL",
    "ESTATES",
    "REALTY",
    "INVESTMENTS",
    "INVESTMENT",
    "GROUP",
    "ASSOCIATION",
    "ASSOCIATES",
    "ASSOC",
    "HOMES",
    "HOME",
    "BUILDERS",
    "CONSTRUCTION",
    "DEVELOPMENT",
    "DEVELOPERS",
    "CAPITAL",
    "VENTURES",
    "ENTERPRISES",
    "MANAGEMENT",
    "SERVICES",
    "RENTALS",
    "LAND",
    "LOTS",
    "RESIDENCES",
    "STREET",
    "DRIVE",
    "ROAD",
    "AVENUE",
    "LANE",
    "COURT",
    "CHURCH",
    "BAPTIST",
    "MINISTRIES",
    "BANK",
    "NA",
    "FSB",
    "MORTGAGE",
    "CITY",
    "COUNTY",
    "STATE",
    "NORTH",
    "CAROLINA",
    "CHARLOTTE",
    "MECKLENBURG",
    "HOUSING",
    "AUTHORITY",
    "SCHOOL",
    "SCHOOLS",
    "BOARD",
    "EDUCATION",
    "UNITED",
    "STATES",
    "AMERICA",
    "C/O",
    "ATTN",
    "JR",
    "SR",
    "II",
    "III",
    "IV",
    "MD",
    "DDS",
    "ETAL",
    "ET",
    "AL",
    "ETUX",
    "UX",
    "ETVIR",
    "VIR",
    "DECEASED",
    "DECD",
    "EXECUTOR",
    "EXECUTRIX",
    "ADMINISTRATOR",
    "ADMINISTRATRIX",
    "DEVISEES",
    "LIFE",
    "TENANT",
    "SUCCESSOR",
    "DATED",
    "DTD",
    "UNDER",
    "AS",
    "FOR",
    "BY",
    "A",
    "AN",
    "TO",
    "IN",
    "AT",
    "ON",
    "TRUSTEES",
    "APARTMENTS",
    "CONDOMINIUM",
    "OWNERS",
    "PARTNERS",
    "FUND",
    "REIT",
    "SERIES",
    "PORTFOLIO",
    "HOLDING",
    "TITLE",
    "SUBSTITUTE",
    "SERVICING",
    "LENDING",
    "FINANCIAL",
    "FINANCE",
    "CREDIT",
    "UNION",
    "NATIONAL",
    "FEDERAL",
    "SAVINGS",
    "LOAN",
    "INVITATION",
    "PROGRESS",
    "RESIDENTIAL",
    "AMERICAN",
    "OPPORTUNITY",
    "ZONE",
    "SOLUTIONS",
    "SYSTEMS",
    "NETWORK",
    "ENERGY",
    "GAS",
    "POWER",
    "ELECTRIC",
    "DUKE",
    "PIEDMONT",
    "NATURAL",
    "RAILROAD",
    "RAILWAY",
    "TELEPHONE",
    "CABLE",
    "WATER",
    "SEWER",
    "DEPARTMENT",
    "TRANSPORTATION",
    "NCDOT",
    "HABITAT",
    "HUMANITY",
    "SALVATION",
    "ARMY",
    "GOODWILL",
    "YMCA",
    "HOSPITAL",
    "HEALTH",
    "MEDICAL",
    "UNIVERSITY",
    "COLLEGE",
    "DIOCESE",
    "CEMETERY",
    "CLUB",
    "LODGE",
    "MASONIC",
    "TEMPLE",
    "MOSQUE",
    "SYNAGOGUE",
}
HTML_WORDS = {
    "TABLE",
    "TBODY",
    "THEAD",
    "TR",
    "TD",
    "TH",
    "DIV",
    "SPAN",
    "INPUT",
    "SELECT",
    "OPTION",
    "FORM",
    "SCRIPT",
    "STYLE",
    "HTML",
    "HEAD",
    "BODY",
    "TITLE",
    "META",
    "LINK",
    "IMG",
    "SRC",
    "HREF",
    "CLASS",
    "ID",
    "NAME",
    "VALUE",
    "TYPE",
    "TEXT",
    "HIDDEN",
    "WIDTH",
    "HEIGHT",
    "BORDER",
    "COLOR",
    "FONT",
    "SIZE",
    "ALIGN",
    "CENTER",
    "LEFT",
    "RIGHT",
    "TOP",
    "BOTTOM",
    "PADDING",
    "MARGIN",
    "DISPLAY",
    "NONE",
    "BLOCK",
    "INLINE",
    "VISIBILITY",
    "VISIBLE",
    "POSITION",
    "ABSOLUTE",
    "RELATIVE",
    "FIXED",
    "OVERFLOW",
    "AUTO",
    "SOLID",
    "FUNCTION",
    "VAR",
    "RETURN",
    "TRUE",
    "FALSE",
    "NULL",
    "THIS",
    "NEW",
    "IF",
    "ELSE",
    "FOR",
    "WHILE",
    "CASE",
    "BREAK",
    "SWITCH",
    "DEFAULT",
    "DOCUMENT",
    "WINDOW",
    "LOCATION",
    "ALERT",
    "ONCLICK",
    "ONCHANGE",
    "ONLOAD",
    "EVENT",
    "TARGET",
    "ARGUMENT",
    "VIEWSTATE",
    "EVENTVALIDATION",
    "SUBMIT",
    "BUTTON",
    "CHECKBOX",
    "RADIO",
    "LABEL",
    "DATA",
    "VAL",
    "KEY",
    "IDX",
    "ROW",
    "ROWS",
    "COL",
    "COLS",
    "CELL",
    "GRID",
    "PAGE",
    "SORT",
    "ASC",
    "DESC",
    "IMAGE",
    "IMAGES",
    "VIEW",
    "COPY",
    "PRINT",
    "SEARCH",
    "RESULTS",
    "RESULT",
    "RECORDS",
    "RECORD",
    "FOUND",
    "SHOWING",
    "THROUGH",
    "TEMP",
    "STATUS",
    "TYPE",
    "BOOK",
    "LEGAL",
    "DESCRIPTION",
    "INSTRUMENT",
    "PARTY",
    "REVERSE",
    "DATE",
    "FILED",
    "DOCUMENT",
    "GRANTOR",
    "GRANTEE",
    "OTHER",
    "NAMES",
    "MORE",
    "SELECTED",
    "ITEM",
    "ITEMS",
    "LIST",
    "OPTIONS",
    "REFINE",
    "FREE",
    "CLEAN",
    "UNOFFICIAL",
    "SESSION",
    "TIMEOUT",
    "WARNING",
    "KEEP",
    "WORKING",
    "END",
    "CANCEL",
    "ESCAPE",
    "ESC",
    "PRESS",
    "CLICK",
    "HERE",
    "LOGIN",
    "LOGON",
    "PASSWORD",
    "REQUIRED",
    "PUBLIC",
    "PRIVATE",
    "COMPUTER",
    "REGISTER",
    "DEEDS",
    "WEB",
    "ACCESS",
    "WELCOME",
    "VISITOR",
    "BASKET",
    "INDEX",
    "REAL",
    "ESTATE",
    "BIRTH",
    "DEATH",
    "MARRIAGE",
    "NOTARY",
    "UCC",
    "LINKS",
    "ASSUMED",
    "INFORMATION",
    "FEES",
    "HISTORICAL",
    "OFFICIAL",
    "COPIES",
    "FAQ",
    "CRITERIA",
    "ACT",
    "AND",
    "ON",
    "OF",
    "THE",
    "IS",
    "AS",
    "AM",
    "PM",
    "COUNT",
    "AGAIN",
    "GET",
    "SET",
    "ELECTRICBLUE",
    "IGEDE",
    "IGG",
    "IGTE",
    "IGMC",
    "MKR",
    "ADR",
    "HDR",
    "SKP",
    "TAG",
    "EXP",
    "IEP",
    "PPD",
    "CHLGCNT",
    "OPR",
}

SYLLABLES = ["bar", "den", "fal", "gor", "hel", "kin", "lor", "mar", "nel", "por", "ral", "sen", "tal", "ver", "wil", "yor", "zan", "bel", "cor", "dal"]
NAME_FIELDS_JSON = {"ownerlastname", "ownerfirstname", "cownerlastname", "cownerfirstname", "grantor", "Customer_Name", "Inspector"}
ID_FIELDS = {
    "pid",
    "nc_pin",
    "commonpid",
    "taxpid",
    "zipcode",
    "GLOBAL_ID",
    "OPR_ID",
    "ParcelId",
    "ParcelID",
    "TaxParcelID",
    "GISParcelID",
    "AddressID",
    "CaseNumber",
    "LienNo",
    "InvoiceNo",
    "CustomerID",
    "ReqNum311",
}
TOKEN_RE = re.compile(r"[A-Za-z]+")  # hyphenated and apostrophe names are handled per alphabetic run


def _salt() -> str:
    s = os.environ.get("MCLT_PSEUDONYM_SALT")
    if not s:
        sys.exit("set MCLT_PSEUDONYM_SALT (any long random string); it is what keeps the mapping unguessable")
    return s


def fake_word(token: str, salt: str) -> str:
    """Three or four syllables: unlike any real surname, stable per (salt, token)."""
    h = hashlib.sha256((salt + "|" + token).encode()).digest()
    return "".join(SYLLABLES[h[i] % len(SYLLABLES)] for i in range(3 + h[5] % 2)).upper()


def name_tokens(value: str):
    for tok in TOKEN_RE.findall(value or ""):
        up = tok.upper()
        if len(up) > 2 and up not in KEEP and up not in HTML_WORDS:
            yield up


def collect(src: str):
    """Pass 1: every token that appears in a person-name field anywhere in the capture."""
    tokens = set()
    for root, _, files in os.walk(src):
        for name in files:
            path = os.path.join(root, name)
            if name.endswith(".json"):
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                for feat in data.get("features", []):
                    a = feat.get("attributes") or {}
                    for k in NAME_FIELDS_JSON:
                        if isinstance(a.get(k), str):
                            tokens.update(name_tokens(a[k]))
                    for k in ("mailaddr1", "mailaddr2", "DetailedDescription", "Property_Address"):
                        v = a.get(k)
                        if isinstance(v, str) and re.search(r"\b(C/O|ATTN|ATTENTION|%)\b", v, flags=re.I):
                            tokens.update(name_tokens(v))
            elif name.endswith(".html"):
                with open(path, encoding="utf-8", errors="replace") as f:
                    html = f.read()
                for m in re.finditer(r'lblT(?:or|ee)"[^>]*>([^<]+)<', html):
                    tokens.update(name_tokens(m.group(1)))
                for m in re.finditer(r"\[R\]([^<\[]+)\[E\]([^<]+)<", html):
                    tokens.update(name_tokens(m.group(1)))
                    tokens.update(name_tokens(m.group(2)))
    return tokens


def make_replacer(tokens, salt):
    mapping = {t: fake_word(t, salt) for t in tokens}

    def replace_text(text: str) -> str:
        def repl(m):
            tok = m.group(0)
            up = tok.upper()
            fake = mapping.get(up)
            if not fake:
                return tok
            return fake if tok.isupper() else (fake.title() if tok[:1].isupper() else fake.lower())

        return TOKEN_RE.sub(repl, text)

    return replace_text


def apply_json(obj, replace_text):
    if isinstance(obj, dict):
        return {k: (v if k in ID_FIELDS else apply_json(v, replace_text)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [apply_json(x, replace_text) for x in obj]
    if isinstance(obj, str):
        if "@" in obj and "." in obj:
            return "inspector@example.invalid"
        if re.fullmatch(r"[\d\.\-() ]{10,}", obj):
            return "704-555-0100"
        return replace_text(obj)
    return obj


def apply_html(html: str, replace_text) -> str:
    """Pass 2 for HTML: the whole document, because Infragistics repeats every cell value in a
    data-ig attribute. Name tokens never collide with markup words (HTML_WORDS is excluded
    from the mapping), so tags, ids and scripts come through unchanged."""
    return replace_text(html)


def main(src: str, dst: str) -> None:
    salt = _salt()
    tokens = collect(src)
    replace_text = make_replacer(tokens, salt)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    for root, _, files in os.walk(src):
        rel = os.path.relpath(root, src)
        if rel.startswith("golden"):
            continue
        os.makedirs(os.path.join(dst, rel), exist_ok=True)
        for name in files:
            sp, dp = os.path.join(root, name), os.path.join(dst, rel, name)
            if name.endswith(".json"):
                with open(sp, encoding="utf-8") as f:
                    data = json.load(f)
                with open(dp, "w", encoding="utf-8") as f:
                    json.dump(apply_json(data, replace_text), f, indent=1, sort_keys=True)
            elif name.endswith(".html"):
                with open(sp, encoding="utf-8", errors="replace") as f:
                    html = f.read()
                with open(dp, "w", encoding="utf-8") as f:
                    f.write(apply_html(html, replace_text))
            else:
                shutil.copy(sp, dp)
    print("pseudonymized {0} name tokens: {1} -> {2}".format(len(tokens), src, dst))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
