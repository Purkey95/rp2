---
name: triage-issues
description: Label and summarize new GitHub issues; never close, never reply with fixes
when: triage reports unlabeled open issues
---
Steps:
1. `gh issue list --limit 20` — pick unlabeled ones.
2. Add labels (bug/enhancement/question/country-plugin). One-line summary in STATE.md.
3. Anything mentioning wrong tax numbers = contract-sensitive, queue for human.

Never:
- Never close an issue.
- Never promise a fix or a timeline in a comment.

Done when: no unlabeled issues remain.
