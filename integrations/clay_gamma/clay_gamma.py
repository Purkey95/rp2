#!/usr/bin/env python3
# Copyright 2026 purkey95
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

"""Clay -> Gamma personalized-microsite generator.

Takes prospect rows enriched in Clay (CSV/JSON export, or live HTTP pushes
from a Clay "HTTP API" column) and generates a branded Gamma webpage,
presentation, or document per prospect via the Gamma Generations API v1.0.

Stdlib only — no dependencies. Usable as a CLI or imported as a module.

Commands:
    generate  Batch-generate one gamma per prospect row from a CSV/JSON file.
    serve     Run a small webhook server that Clay's HTTP API column can call.
    themes    List workspace themes (to pick a --theme-id for branding).

Authentication: set GAMMA_API_KEY (Gamma: Account Settings > API Keys).
"""

import argparse
import csv
import json
import logging
import os
import string
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib import error, request

API_BASE: str = os.environ.get("GAMMA_API_BASE", "https://public-api.gamma.app/v1.0")
POLL_INTERVAL_SECONDS: float = 5.0
DEFAULT_TIMEOUT_SECONDS: float = 600.0

DEFAULT_PROMPT_TEMPLATE: str = """\
Create a personalized one-page proposal microsite for {company}.

About the prospect:
- Company: {company}
- Contact: {first_name} {last_name}, {title}
- Industry: {industry}
- Website: {domain}
- What they do: {description}

The page should:
1. Open with a headline addressed to {company}.
2. Summarize how our offering solves problems specific to the {industry} industry.
3. Include a short section of anticipated ROI / benefits.
4. End with a clear call to action to book a call.
"""

LOGGER: logging.Logger = logging.getLogger("clay_gamma")


class GammaAPIError(Exception):
    """Raised when the Gamma API returns an error response."""


class _SafeDict(dict):
    """Leaves unknown {placeholders} intact instead of raising KeyError."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_template(template: str, row: Dict[str, Any]) -> str:
    cleaned: Dict[str, str] = {str(k).strip(): str(v).strip() for k, v in row.items() if v is not None}
    return string.Formatter().vformat(template, (), _SafeDict(cleaned))


class GammaClient:
    """Minimal Gamma Generations API v1.0 client (stdlib urllib)."""

    def __init__(self, api_key: str, base_url: str = API_BASE):
        self.api_key: str = api_key
        self.base_url: str = base_url.rstrip("/")

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url: str = f"{self.base_url}{path}"
        data: Optional[bytes] = json.dumps(body).encode("utf-8") if body is not None else None
        req: request.Request = request.Request(
            url,
            data=data,
            method=method,
            headers={"X-API-KEY": self.api_key, "Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with request.urlopen(req, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail: str = exc.read().decode("utf-8", errors="replace")
            raise GammaAPIError(f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise GammaAPIError(f"{method} {path} failed: {exc.reason}") from exc

    def create_generation(
        self,
        input_text: str,
        gamma_format: str = "webpage",
        theme_id: Optional[str] = None,
        num_cards: Optional[int] = None,
        additional_instructions: Optional[str] = None,
        text_options: Optional[Dict[str, str]] = None,
        image_options: Optional[Dict[str, str]] = None,
        export_as: Optional[str] = None,
        title: Optional[str] = None,
    ) -> str:
        """Start a generation and return its generationId."""
        body: Dict[str, Any] = {"inputText": input_text, "format": gamma_format}
        if theme_id:
            body["themeId"] = theme_id
        if num_cards:
            body["numCards"] = num_cards
        if additional_instructions:
            body["additionalInstructions"] = additional_instructions
        if text_options:
            body["textOptions"] = text_options
        if image_options:
            body["imageOptions"] = image_options
        if export_as:
            body["exportAs"] = export_as
        if title:
            body["title"] = title[:500]
        result: Dict[str, Any] = self._request("POST", "/generations", body)
        generation_id: Optional[str] = result.get("generationId") or result.get("id")
        if not generation_id:
            raise GammaAPIError(f"No generationId in response: {result}")
        return generation_id

    def get_generation(self, generation_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/generations/{generation_id}")

    def wait_for_generation(self, generation_id: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Dict[str, Any]:
        """Poll until the generation completes or fails. Returns the final payload."""
        deadline: float = time.monotonic() + timeout
        while True:
            result: Dict[str, Any] = self.get_generation(generation_id)
            status: str = result.get("status", "pending")
            if status in ("completed", "failed"):
                return result
            if time.monotonic() >= deadline:
                raise GammaAPIError(f"Generation {generation_id} timed out after {timeout:.0f}s")
            time.sleep(POLL_INTERVAL_SECONDS)

    def list_themes(self) -> List[Dict[str, Any]]:
        themes: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            path: str = "/themes" + (f"?after={cursor}" if cursor else "")
            result: Dict[str, Any] = self._request("GET", path)
            themes.extend(result.get("data", []))
            cursor = result.get("nextCursor")
            if not result.get("hasMore") or not cursor:
                return themes


def build_prompt(row: Dict[str, Any], template: str, scheduling_link: Optional[str]) -> str:
    prompt: str = render_template(template, row)
    if scheduling_link:
        prompt += (
            f"\nInclude a prominent call-to-action button linking to {scheduling_link} "
            f"with text like 'Book a call'."
        )
    return prompt


def generate_for_row(
    client: GammaClient,
    row: Dict[str, Any],
    args: argparse.Namespace,
    template: str,
) -> str:
    prompt: str = build_prompt(row, template, args.scheduling_link)
    text_options: Dict[str, str] = {}
    if args.tone:
        text_options["tone"] = args.tone
    if args.audience:
        text_options["audience"] = args.audience
    if args.text_amount:
        text_options["amount"] = args.text_amount
    image_options: Dict[str, str] = {"source": args.image_source} if args.image_source else {}
    title: Optional[str] = None
    if row.get("company"):
        title = f"{args.title_prefix}{row['company']}" if args.title_prefix else str(row["company"])
    return client.create_generation(
        input_text=prompt,
        gamma_format=args.format,
        theme_id=args.theme_id,
        num_cards=args.num_cards,
        additional_instructions=args.additional_instructions,
        text_options=text_options or None,
        image_options=image_options or None,
        export_as=args.export_as,
        title=title,
    )


def load_rows(path: str) -> List[Dict[str, Any]]:
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as json_file:
            data: Any = json.load(json_file)
        if isinstance(data, dict):
            data = data.get("rows", [data])
        return list(data)
    with open(path, newline="", encoding="utf-8") as csv_file:
        return list(csv.DictReader(csv_file))


def command_generate(client: GammaClient, args: argparse.Namespace) -> int:
    rows: List[Dict[str, Any]] = load_rows(args.input)
    if not rows:
        LOGGER.error("No rows found in %s", args.input)
        return 1
    template: str = DEFAULT_PROMPT_TEMPLATE
    if args.prompt_template:
        with open(args.prompt_template, encoding="utf-8") as template_file:
            template = template_file.read()

    # Create all generations first, then poll: Gamma runs them server-side in
    # parallel, so total wall-clock is one generation, not the sum.
    pending: List[Dict[str, Any]] = []
    for index, row in enumerate(rows):
        try:
            generation_id: str = generate_for_row(client, row, args, template)
            LOGGER.info("Row %d (%s): started generation %s", index + 1, row.get("company", "?"), generation_id)
            pending.append({"row": row, "generationId": generation_id})
        except GammaAPIError as exc:
            LOGGER.error("Row %d (%s): %s", index + 1, row.get("company", "?"), exc)
            pending.append({"row": row, "generationId": None, "error": str(exc)})

    results: List[Dict[str, Any]] = []
    for item in pending:
        row = item["row"]
        record: Dict[str, Any] = dict(row)
        record["generation_id"] = item.get("generationId") or ""
        if not item.get("generationId"):
            record.update(status="failed", gamma_url="", export_url="", error=item.get("error", ""))
            results.append(record)
            continue
        try:
            final: Dict[str, Any] = client.wait_for_generation(item["generationId"], timeout=args.timeout)
            record["status"] = final.get("status", "")
            record["gamma_url"] = final.get("gammaUrl", "")
            record["export_url"] = final.get("exportUrl", "")
            record["error"] = (final.get("error") or {}).get("message", "") if final.get("status") == "failed" else ""
            credits: Dict[str, Any] = final.get("credits") or {}
            if credits:
                LOGGER.info("Credits: deducted=%s remaining=%s", credits.get("deducted"), credits.get("remaining"))
        except GammaAPIError as exc:
            record.update(status="failed", gamma_url="", export_url="", error=str(exc))
        LOGGER.info("Row (%s): %s %s", row.get("company", "?"), record["status"], record["gamma_url"])
        results.append(record)

    if args.output.endswith(".json"):
        with open(args.output, "w", encoding="utf-8") as json_out:
            json.dump(results, json_out, indent=2)
    else:
        fieldnames: List[str] = sorted({key for record in results for key in record})
        with open(args.output, "w", newline="", encoding="utf-8") as csv_out:
            writer: csv.DictWriter = csv.DictWriter(csv_out, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
    LOGGER.info("Wrote %d results to %s", len(results), args.output)
    return 0 if all(record.get("status") == "completed" for record in results) else 1


def command_themes(client: GammaClient, _args: argparse.Namespace) -> int:
    for theme in client.list_themes():
        print(f"{theme.get('id')}\t{theme.get('type', '')}\t{theme.get('name', '')}")
    return 0


def make_webhook_handler(client: GammaClient, args: argparse.Namespace, template: str) -> type:
    """Webhook for Clay's HTTP API column: POST a JSON object of row fields,

    receive {"status", "gamma_url", "generation_id"}. With ?async=1 the
    response returns the generation_id immediately without waiting.
    """

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server API
            try:
                length: int = int(self.headers.get("Content-Length", "0"))
                row: Dict[str, Any] = json.loads(self.rfile.read(length).decode("utf-8"))
                generation_id: str = generate_for_row(client, row, args, template)
                if "async=1" in (self.path.split("?", 1) + [""])[1]:
                    payload: Dict[str, Any] = {"status": "pending", "generation_id": generation_id, "gamma_url": ""}
                else:
                    final: Dict[str, Any] = client.wait_for_generation(generation_id, timeout=args.timeout)
                    payload = {
                        "status": final.get("status", ""),
                        "generation_id": generation_id,
                        "gamma_url": final.get("gammaUrl", ""),
                        "export_url": final.get("exportUrl", ""),
                    }
                self._reply(200, payload)
            except (GammaAPIError, ValueError) as exc:
                self._reply(502, {"status": "failed", "error": str(exc)})

        def do_GET(self) -> None:  # noqa: N802 - status check: GET /<generation_id>
            generation_id: str = self.path.strip("/").split("?", 1)[0]
            if not generation_id:
                self._reply(200, {"status": "ok", "service": "clay_gamma"})
                return
            try:
                final: Dict[str, Any] = client.get_generation(generation_id)
                self._reply(
                    200,
                    {
                        "status": final.get("status", ""),
                        "generation_id": generation_id,
                        "gamma_url": final.get("gammaUrl", ""),
                        "export_url": final.get("exportUrl", ""),
                    },
                )
            except GammaAPIError as exc:
                self._reply(502, {"status": "failed", "error": str(exc)})

        def _reply(self, code: int, payload: Dict[str, Any]) -> None:
            body: bytes = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *log_args: Any) -> None:
            LOGGER.info("%s - %s", self.address_string(), fmt % log_args)

    return Handler


def command_serve(client: GammaClient, args: argparse.Namespace) -> int:
    template: str = DEFAULT_PROMPT_TEMPLATE
    if args.prompt_template:
        with open(args.prompt_template, encoding="utf-8") as template_file:
            template = template_file.read()
    server: ThreadingHTTPServer = ThreadingHTTPServer(
        (args.host, args.port), make_webhook_handler(client, args, template)
    )
    LOGGER.info("Webhook listening on http://%s:%d (POST prospect JSON; GET /<generation_id> to poll)", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Shutting down")
    return 0


def add_generation_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", default="webpage", choices=["webpage", "presentation", "document", "social"])
    parser.add_argument("--theme-id", help="Workspace theme for branding (see the 'themes' command)")
    parser.add_argument("--num-cards", type=int, help="Target number of cards/sections")
    parser.add_argument("--additional-instructions", help="Extra steering passed to Gamma (max 5000 chars)")
    parser.add_argument("--tone", help="textOptions.tone, e.g. 'professional, confident'")
    parser.add_argument("--audience", help="textOptions.audience, e.g. 'VP of Sales at mid-market SaaS'")
    parser.add_argument("--text-amount", choices=["brief", "medium", "detailed", "extensive"])
    parser.add_argument("--image-source", help="imageOptions.source, e.g. aiGenerated, noImages, themeAccent")
    parser.add_argument("--export-as", choices=["pdf", "pptx", "png"], help="Also export each gamma")
    parser.add_argument("--scheduling-link", help="Calendly/booking URL to inject as the page CTA")
    parser.add_argument("--title-prefix", default="", help="Prefix for gamma titles, e.g. 'Proposal for '")
    parser.add_argument("--prompt-template", help="Path to a prompt template with {field} placeholders")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="Seconds to wait per generation")


def main() -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(prog="clay_gamma", description=__doc__)
    parser.add_argument("--api-key", default=os.environ.get("GAMMA_API_KEY"), help="Gamma API key (or set GAMMA_API_KEY)")
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser: argparse.ArgumentParser = subparsers.add_parser("generate", help="Batch-generate from a Clay export")
    generate_parser.add_argument("input", help="CSV or JSON file of prospect rows (Clay export)")
    generate_parser.add_argument("-o", "--output", default="gamma_results.csv", help="Results CSV/JSON path")
    add_generation_options(generate_parser)

    serve_parser: argparse.ArgumentParser = subparsers.add_parser("serve", help="Webhook server for Clay HTTP API columns")
    serve_parser.add_argument("--host", default="0.0.0.0")
    serve_parser.add_argument("--port", type=int, default=8080)
    add_generation_options(serve_parser)

    subparsers.add_parser("themes", help="List workspace themes")

    args: argparse.Namespace = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not args.api_key:
        parser.error("Gamma API key required: pass --api-key or set GAMMA_API_KEY")
    client: GammaClient = GammaClient(args.api_key)
    if args.command == "generate":
        return command_generate(client, args)
    if args.command == "serve":
        return command_serve(client, args)
    return command_themes(client, args)


if __name__ == "__main__":
    sys.exit(main())
