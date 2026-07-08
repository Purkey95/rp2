# Clay → Gamma Personalized Microsite Generator

Automates the "enrich in Clay → generate a branded, personalized proposal
page in Gamma → drop a scheduling link on it" ABM workflow, using the
[Gamma Generations API v1.0](https://developers.gamma.app/) (GA since
November 2025; the old v0.2 API is sunset).

This directory is **self-contained and dependency-free** (Python 3.8+
stdlib only), so it can be lifted wholesale into another tool or repo —
e.g. a `monitorctl`-style CLI — by copying `clay_gamma.py`.

## Setup

1. Get a Gamma API key: Gamma → Account Settings → API Keys (paid
   workspaces; generations consume Gamma credits).
2. `export GAMMA_API_KEY=sk-gamma-...`
3. Pick a branded theme: `python3 clay_gamma.py themes` and note the id
   of your workspace's custom theme.

## Usage

### Batch mode (Clay CSV/JSON export)

Export your enriched table from Clay (company, first_name, title,
industry, domain, description — any columns work; they become
`{placeholders}` in the prompt template):

```bash
python3 clay_gamma.py generate prospects.csv \
    --format webpage \
    --theme-id <your-brand-theme-id> \
    --scheduling-link https://calendly.com/you/intro \
    --tone "confident, consultative" \
    --prompt-template prompt_template.txt \
    -o results.csv
```

All generations are created up front and then polled, so a 50-row batch
takes roughly as long as one generation. `results.csv` contains each
input row plus `status`, `gamma_url` (the personalized microsite link to
put in your outreach), `export_url`, and `generation_id`.

### Live mode (Clay "HTTP API" column)

Run the webhook somewhere Clay can reach (or expose locally via a tunnel):

```bash
python3 clay_gamma.py serve --port 8080 \
    --theme-id <id> --scheduling-link https://calendly.com/you/intro
```

In Clay, add an **HTTP API** enrichment column:

- Method `POST`, URL `https://your-host:8080/`
- Body: JSON mapping of the row's fields, e.g.
  `{"company": "/Company Name", "industry": "/Industry", ...}`
- The response `gamma_url` field is the personalized page — map it into a
  new column and merge it into your email/sequence templates.

Generations take ~30–60 s. If Clay times out waiting, call
`POST /?async=1` instead (returns `generation_id` immediately) and add a
second HTTP API column that polls `GET /<generation_id>` until
`status: completed`.

### No middleware at all

Clay can also call Gamma directly from an HTTP API column —
`POST https://public-api.gamma.app/v1.0/generations` with an `X-API-KEY`
header — plus a second polling column for
`GET /v1.0/generations/{generationId}`. This tool exists for what that
approach can't do: prompt templating from row fields, retry/timeout
handling, batch runs from exports, and keeping the API key out of Clay.

## Prompt templates

`prompt_template.txt` shows the format: plain text with `{column_name}`
placeholders filled from each row (unknown placeholders are left as-is,
so a sparse row won't crash the run). `--scheduling-link` appends a CTA
instruction automatically.

## Notes

- Rate limits: Gamma returns `x-ratelimit-*` headers; the built-in 5 s
  poll interval stays well within them.
- Each generation deducts Gamma credits; the tool logs
  `deducted`/`remaining` after each completion.
- This workflow works identically with Clay alternatives (Apollo, n8n,
  Make, a plain spreadsheet) — anything that can produce a CSV or POST
  JSON rows at the webhook.
