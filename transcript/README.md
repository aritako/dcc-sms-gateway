# Session transcripts

The assignment requires a **complete AI coding transcript or session export**. Store two files per session in this directory:

- `NN-kebab-case.json` — the authoritative complete OpenCode session export. It contains every user/assistant message and tool call.
- `NN-kebab-case.md` — a readable summary/index of the same session. It is for humans and links to the durable artifacts.

Markdown summaries render on GitHub and diff cleanly; the JSON export is the machine-readable evidence. Keep both.

## Naming

`NN-kebab-case`, zero-padded and monotonic:

- `01-setup-tickets.json` + `01-setup-tickets.md`
- `02-walking-skeleton.json` + `02-walking-skeleton.md`
- `03-send-api.json` + `03-send-api.md`

Never renumber. If a session resumes in the same thread, append to its existing files.

## Complete export

From the session's project directory:

```bash
opencode session export "$OPENCODE_SESSION_ID" > transcript/NN-kebab-case.json
```

Then review and redact secrets before committing. Do **not** use `opencode session export --sanitize` blindly: it also redacts the actual user prompts, which defeats the purpose of the transcript. Prefer a targeted redaction pass for tokens, passwords and private keys.

The JSON may include harness/system messages (for example, instruction updates). Review them for sensitive content; keep them when they are part of the session.

## Markdown summary

Start with metadata:

```markdown
# Session NN — Title

- **Date:** YYYY-MM-DD
- **Agent/model:** provider/model
- **Goal:** what the session set out to do
- **Outcome:** what landed, what remains
- **Issues/commits:** links or SHAs
- **Complete export:** [NN-kebab-case.json](NN-kebab-case.json)
```

Then record the conversation: every user message, the assistant's decisions and conclusions, and a concise tool-activity log. The JSON is the full record; the Markdown should make it easy to find the decisions and artifacts.

## Template

````markdown
# Session 01 — Setup and ticket breakdown

- **Date:** 2026-11-18
- **Agent/model:** opencode-go/deepseek-v4.1-flash
- **Goal:** Turn the assignment into implementable GitHub issues.
- **Outcome:** 27 issues created; labels and dependency edges wired.
- **Issues/commits:** #1–#27
- **Complete export:** [01-setup-tickets.json](01-setup-tickets.json)

## Transcript

### User

...

### Assistant

...

### Tool activity

- `gh issue create ...` — created issues #1–#27.
````
