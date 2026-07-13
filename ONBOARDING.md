# Fable-director — team onboarding (auto-updating install)

One paste, one restart, then updates arrive on their own. No CLI, no UI clicks.

## The prompt (paste into any Claude Code session)

```
Configure the fable-director plugin with automatic updates. Do exactly this:

1. Open ~/.claude/settings.json (create it with {} if missing). Back it up first.
2. MERGE-edit, preserving every existing key:
   - In "extraKnownMarketplaces": if a "pixelfarm" entry with a "directory"
     source already exists, replace it; otherwise add:
       "pixelfarm": {
         "source": { "source": "github", "repo": "frsorrentino/fable-director" },
         "autoUpdate": true
       }
   - In "enabledPlugins" add: "fable-director@pixelfarm": true
3. Re-validate the JSON (it must parse). If invalid, restore the backup and tell me.
4. Touch nothing else in the file. Do not use "claude plugin" commands.
5. Confirm what you wrote and tell me to restart Claude Code once.
```

## What happens next

| Phase | User action |
|---|---|
| Setup | paste the prompt, restart Claude Code **once** |
| Every future release | **none** — Claude Code downloads updates in the background; the next session starts on the new version |

A `/reload-plugins` notification may appear after an update: it is optional — it only
applies the new version to the *current* session instead of the next one.

## After the restart (one-off, recommended)

1. Initialize the playbook (lives outside the plugin, updates never touch it):
   `cp <cache>/fable-director/playbook-template.md ~/.claude/delega-playbook.md`
   — or in-session: ask Claude to do it, or see [INSTALL.md](INSTALL.md) § 3.
2. Enable the statusline: `/fable-director:statusline` → restart.
3. Optional but worth it: connect free-tier external executors — [INSTALL.md](INSTALL.md) § 4b.

## Migrating from a zip / local-directory install

The prompt above already handles it: it replaces the local `pixelfarm` marketplace entry
with the GitHub one. If anything looks stuck afterwards, run once:

```bash
claude plugin uninstall fable-director
claude plugin marketplace add frsorrentino/fable-director
claude plugin install fable-director@pixelfarm --scope user
```
