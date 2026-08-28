# Raw Sources

Drop source documents here: markdown articles (Obsidian Web Clipper output
works great), notes, papers, transcripts. Images go in `assets/`.

This directory is the source of truth and is **immutable to the LLM** — it
reads from here but never edits, renames, or deletes anything. To get a
source into the wiki, tell Claude: `ingest raw/<filename>`.
