"""Turn HTML tables into rows of {header: cell}. County portals are mostly tables.

stdlib html.parser only. Header cells come from <th>, or from the first row when a
table has none. Nested tags inside cells are flattened to text; <br> becomes a space.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any, Dict, List, Optional


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: List[List[List[str]]] = []
        self.header_flags: List[List[bool]] = []
        self._row: Optional[List[str]] = None
        self._row_is_header: bool = False
        self._cell: Optional[List[str]] = None
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "table":
            self._depth += 1
            self.tables.append([])
            self.header_flags.append([])
        elif tag == "tr" and self._depth:
            self._row = []
            self._row_is_header = False
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            if tag == "th":
                self._row_is_header = True
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._depth:
            self.tables[-1].append(self._row)
            self.header_flags[-1].append(self._row_is_header)
            self._row = None
        elif tag == "table" and self._depth:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def parse_tables(html: "str | bytes") -> List[List[Dict[str, str]]]:
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")
    parser = _TableParser()
    parser.feed(html)
    out: List[List[Dict[str, str]]] = []
    for rows, flags in zip(parser.tables, parser.header_flags):
        if not rows:
            out.append([])
            continue
        header_idx = next((i for i, f in enumerate(flags) if f), 0)
        header = [h or "col{0}".format(i) for i, h in enumerate(rows[header_idx])]
        body = [r for i, r in enumerate(rows) if i != header_idx and r]
        out.append([dict(zip(header, r + [""] * (len(header) - len(r)))) for r in body])
    return out


def find_table(html: "str | bytes", required_headers: List[str]) -> List[Dict[str, str]]:
    """The first table whose headers cover required_headers (case-insensitive)."""
    wanted = {h.lower() for h in required_headers}
    for table in parse_tables(html):
        if table and wanted <= {k.lower() for k in table[0]}:
            return table
    return []
