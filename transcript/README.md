# Session transcripts

The assignment requires a complete AI coding transcript or session export. Store one Markdown file per session in this directory. Markdown is the canonical format: it renders on GitHub, keeps code blocks and commands readable, and diffs cleanly in Git.

## Naming

`NN-kebab-case.md`, zero-padded and monotonic:

- `01-setup-tickets.md`
- `02-walking-skeleton.md`
- `03-send-api.md`

Never renumber. If a session resumes in the same thread, append to its existing file.

## Content

Start with metadata:

```markdown
# Session NN — Title

- **Date:** YYYY-MM-DD
- **Agent/model:** provider/model
- **Goal:** what the session set out to do
- **Outcome:** what landed, what remains
- **Issues/commits:** links or SHAs
```

Then record the conversation. Include every user and assistant message. For tool activity, include the command and a concise result; append full logs only where needed to reproduce a change. Redact API keys, tokens, and real `.env` values. Do not include hidden system/developer prompts.

## Template

````markdown
# Session 01 — Setup and ticket breakdown

- **Date:** 2026-11-18
- **Agent/model:** opencode-go/deepseek-v4.1-flash
- **Goal:** Turn the assignment into implementable GitHub issues.
- **Outcome:** 27 issues created; labels and dependency edges wired.
- **Issues/commits:** #1–#27

## Transcript

### User

...

### Assistant

...

### Tool activity

- `gh issue create ...` — created issues #1–#27.
````
