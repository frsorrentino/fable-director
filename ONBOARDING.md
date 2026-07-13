# Fable-director — team onboarding (auto-updating install)

## Standard route (from 1.17.0 — two commands, zero maintenance)

```bash
claude plugin marketplace add frsorrentino/fable-director
claude plugin install fable-director@pixelfarm --scope user
```

Done. At the first session the plugin **enables its own auto-update** for this marketplace
and says so in one line. From then on Claude Code downloads new versions in the background
and every new session starts on the latest one — no user action, ever.

- Opting out: set `"autoUpdate": false` under `extraKnownMarketplaces.pixelfarm` in
  `~/.claude/settings.json`. The plugin never overrides an expressed choice (true or false).
- A `/reload-plugins` notification may appear after an update: optional — it only applies
  the new version to the *current* session instead of the next one.

## No-CLI route (paste a prompt instead)

Same result via a settings.json edit — useful when the CLI is unavailable or you prefer
Claude to do it. Paste into any Claude Code session:

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

Then restart Claude Code once.

## After install (one-off, recommended)

1. Initialize the playbook (lives outside the plugin, updates never touch it):
   `cp <cache>/fable-director/playbook-template.md ~/.claude/delega-playbook.md`
   — or ask Claude in-session; see [INSTALL.md](INSTALL.md) § 3.
2. Enable the statusline: `/fable-director:statusline` → restart.
3. Optional but worth it: connect free-tier external executors — [INSTALL.md](INSTALL.md) § 4b.

## Migrating from a zip / local-directory install

Zip and directory installs never self-update (no remote to pull from) — and the sentinel
leaves them alone by design (they are the development route). Migrate once:

```bash
claude plugin uninstall fable-director
claude plugin marketplace add frsorrentino/fable-director
claude plugin install fable-director@pixelfarm --scope user
```

Auto-update then self-enables at the first session, as above.
