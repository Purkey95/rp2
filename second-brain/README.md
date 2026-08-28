# AI Second Brain (Obsidian + Claude Code)

A ready-to-use implementation of [Andrej Karpathy's "LLM Wiki" pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f):
a personal knowledge base where **you curate sources and ask questions, and the
LLM does all the bookkeeping** — summarizing, cross-referencing, filing, and
keeping everything consistent.

> Obsidian is the IDE; the LLM is the programmer; the wiki is the codebase.
> — Karpathy

## The honest 15-minute setup

The viral version of this says "Download Claude Desktop" — that's not quite
right. The agent needs **real filesystem access** to read and write your vault,
which means **Claude Code** (terminal, desktop app, or web) or Claude Desktop
with Cowork / a filesystem MCP server. Here's the working version:

1. **Install [Obsidian](https://obsidian.md)** (free).
2. **Install [Claude Code](https://claude.com/claude-code)** (`npm install -g @anthropic-ai/claude-code`, or use the desktop app).
3. **Open this folder as a vault** in Obsidian ("Open folder as vault" → pick
   `second-brain/`), or copy this folder anywhere you like first.
4. **Open a terminal in this folder and run `claude`.** The `CLAUDE.md` schema
   in this directory loads automatically — Claude already knows how the wiki
   is structured and how to maintain it. No prompt-pasting needed.
5. **Drop a source into `raw/`** (a markdown article, notes, a paper) and say:
   *"Ingest raw/whatever.md"*. Watch the wiki build itself in Obsidian's graph
   view.

That's genuinely it.

## How it works — three layers

| Layer | Directory | Who writes it |
|---|---|---|
| **Raw sources** | `raw/` | You (immutable — the LLM never edits these) |
| **The wiki** | `wiki/` | The LLM (summaries, entity pages, concepts, syntheses) |
| **The schema** | `CLAUDE.md` | You + the LLM, co-evolved over time |

Two navigation files keep it searchable without any RAG infrastructure:

- **`wiki/index.md`** — a catalog of every page with one-line summaries.
  Claude reads this first on every query, then drills into relevant pages.
- **`log.md`** — an append-only timeline of every ingest, query, and lint pass.

## The three operations

- **Ingest** — "Ingest `raw/some-article.md`" → Claude reads it, writes a
  summary page, creates/updates entity and concept pages, wires up
  `[[wikilinks]]`, updates the index, appends to the log. One source can touch
  10–15 pages in a single pass.
- **Query** — "What do my notes say about X?" → Claude reads the index, pulls
  the relevant pages, and synthesizes an answer with citations. Good answers
  get filed back into `wiki/` so your explorations compound too.
- **Lint** — "Lint the wiki" → Claude hunts for contradictions, stale claims,
  orphan pages, and missing cross-references, and suggests what to read next.

## Tips

- **Obsidian Web Clipper** (browser extension) converts any web article to
  markdown — the fastest way to feed `raw/`.
- **Graph view** in Obsidian shows the shape of your wiki: hubs, clusters,
  orphans.
- In Obsidian Settings → Files and links, set the attachment folder to
  `raw/assets/` so clipped images live locally.
- The vault is just markdown in a git repo — you get version history for free.
- As the vault grows past a few hundred pages, add a local search tool like
  [qmd](https://github.com/tobi/qmd); until then, `index.md` is plenty.

## Credits

Pattern by [Andrej Karpathy](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).
This folder is a concrete instantiation of that (intentionally abstract) idea file.
