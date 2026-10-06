# Privacy policy — fable-director

_Last updated: 2026-10-06. Applies to fable-director 1.56.2 and later._

fable-director is a Claude Code plugin that runs on your computer. It has no server, no account and no telemetry of its own. It sends nothing off your machine unless you set up one of the three opt-in paths listed under [What leaves your machine](#what-leaves-your-machine).

## What the plugin reads

- **What Claude Code hands its hooks:** the session id, the model and effort level, the tool name and input of each call, and the path of the session transcript. From the transcript it reads token counts per turn and per subagent, to enforce the declared budget and fill the statusline. It does not copy the transcript anywhere.
- **Your Claude Code configuration:** `settings.json` (statusline, model, plugin marketplaces), `plugins/installed_plugins.json` and `plugins/known_marketplaces.json`, and, only when you run the model check, the `CLAUDE.md`, skills and agents of your accounts.
- **Files you point a tool at:** the video, audio, text or CSV you name when you use the media tools or the anonymizer. Nothing else on disk is scanned.

## What the plugin writes on your disk

- `~/.claude/fable-director/`: telemetry (`telemetry.db`, token counts and events, never message text), open budgets and receipts, subagent and workflow state, handoffs, the media cache (contact sheets and transcripts, per file), the anonymizer maps (`anonymizer/maps/`, mode 0600, the only place where original values sit next to their placeholders), the optional venv for local transcription (`tools/venv`).
- On Claude Code ≥ 2.1.287 with Mods on, the plugin's hooks module writes the same quota files the statusline writes (`quota-<account>.json`, `quota-history-<account>.jsonl`, `usage-snapshot-<account>.json` in `~/.claude/fable-director/`) and reads nothing else. It sends nothing anywhere.
- `~/.claude/settings.json`: the `statusLine` entry at the first session, and, for an install from a GitHub marketplace, `extraKnownMarketplaces.<name>.autoUpdate: true` once. Both are announced in-session, both keep a backup of the file next to it, and an `autoUpdate` you set yourself (true or false) is never touched.
- `${XDG_STATE_HOME:-~/.local/state}/claude-observe/fable-director.jsonl` (mode 0600): failures of the plugin's own scripts, with every argument value replaced by `<ARG>` and the error text scrubbed of home paths, emails, URL queries and secrets. Disable with `{"enabled": false}` in `~/.config/claude-observe/config.json`.
- With `/fable-director:handoff --here`: one Markdown file in the project's `docs/`, on your request.

## What leaves your machine

Nothing, by default. Three paths exist, each used only when you start it:

1. **External model routes** (`cross-verify.py`, `external-exec.py`, the long-video path of `media-analyze.py`). After you write a key or log in to a provider CLI (`cross-verify.py --init`), the claim, rubric, context, spec, input text or media file you pass to that route is sent to that provider: Google Gemini (`generativelanguage.googleapis.com`), the Codex or Antigravity CLI you installed, or xAI (`api.x.ai`, paid, only with `--paid-ok`). A task marked `--data-class restricted` refuses these routes. Keys are read from the config file you wrote or the environment variable you set; they are never written to logs or reports.
2. **Error reports** (`/fable-director:observe send`). The draft is shown to you first and sent only after you choose. It contains script names, subcommands and scrubbed error text, never your files or prompts. You pick one of two ways:
   - **Send from my GitHub:** a GitHub issue in your name (through `gh` or a prefilled link you open), or a private vulnerability report.
   - **Send anonymously:** one HTTPS request to `https://www.francescosorrentino.com/api/observe/report.php`, a service run by the plugin's maintainer. It carries the plugin name and version, two flags (security report, blocking) and the anonymized title and body you were shown — no GitHub account, machine name or identifier; like any request, it carries your IP address. The service publishes the text as an issue (or a private vulnerability report) on this repository and keeps neither the text nor your IP: it keeps only a keyed hash of the IP for rate limiting, deleted daily. Details: [claude-observe privacy note](https://github.com/frsorrentino/claude-observe/blob/main/PRIVACY.md).
3. **Downloads you start:** `media-doctor.py --setup` installs Python packages from PyPI into the venv, and the first transcription downloads the Whisper model you chose from Hugging Face into `~/.cache/huggingface`. Both say what they fetch before they run.

Plugin updates are fetched by Claude Code itself from GitHub, not by the plugin.

## Retention

Everything above stays until you delete it. Telemetry is not rotated by the plugin; delete `~/.claude/fable-director/` to start clean. The anonymizer maps are needed only for `restore`: delete a map when its documents are done. The error log keeps at most what claude-observe's defaults allow and can be disabled.

## People under 18

The plugin is a developer tool and is not intended for people under 18.

## Changes and contact

Changes to this policy are versioned in the [GitHub repository](https://github.com/frsorrentino/fable-director). Questions: open an issue at <https://github.com/frsorrentino/fable-director/issues>; security reports go through [SECURITY.md](../SECURITY.md).
