# ai-tools

My personal collection of AI tooling: Claude Code skills (and whatever comes next). Each skill lives in its own folder under `skills/` with a `SKILL.md` and its own README.

## Skills

| Skill | What it does | Needs |
| --- | --- | --- |
| [`jev-explore`](skills/jev-explore/README.md) | Read-only codebase search that runs like the built-in **Explore** subagent, but uses TypeSafe's Jev model to rank grep/glob candidates before reading them. | A TypeSafe API key (optional: without one it runs as plain Explore), `python3` ≥ 3.9 |

## First-time setup

Do this once per machine after cloning.

### 1. Clone

```bash
git clone git@github.com:KPMGE/ai-tools.git ~/Projects/mine/ai-tools
```

### 2. Install the skills into Claude Code

Claude Code loads personal skills from `~/.claude/skills/<name>/SKILL.md`. Symlink each skill so that a `git pull` updates it in place:

```bash
mkdir -p ~/.claude/skills
for s in ~/Projects/mine/ai-tools/skills/*/; do
  ln -sfn "$s" ~/.claude/skills/"$(basename "$s")"
done
```

If a real directory with the same name is already in `~/.claude/skills` (for example, a copy from before), `ln` creates the link *inside* it. Remove the old copy first (`rm -r ~/.claude/skills/jev-explore`), then run the loop again. To use a skill in one project only, link it into that project's `.claude/skills/` instead.

### 3. Configure each skill's secrets

**jev-explore:** create a key at <https://console.typesafe.ai/keys> (Jev is in early access), then store it:

```bash
mkdir -p ~/.config/typesafe
printf '%s' 'YOUR_KEY' > ~/.config/typesafe/api_key
chmod 600 ~/.config/typesafe/api_key
```

An `export TYPESAFE_API_KEY=...` in your shell profile works too, as does an `env` entry in `~/.claude/settings.json`. See the [skill README](skills/jev-explore/README.md#configure-the-jev-api-key) for the trade-offs.

### 4. Verify

```bash
python3 ~/.claude/skills/jev-explore/scripts/jev.py check   # expect: ok: key accepted ...
```

Then start a new Claude Code session (skills are discovered at startup), type `/`, and check that `jev-explore` is listed. Try it:

```
/jev-explore where is authentication middleware registered
```

## Adding a new skill

1. Create `skills/<name>/SKILL.md` (frontmatter `name` + `description`) plus any `scripts/` it needs. Reference bundled files through `${CLAUDE_SKILL_DIR}`, never absolute paths.
2. Add a `README.md` in the skill folder covering its purpose, configuration and cost.
3. Add a row to the table above and any secrets to **First-time setup → step 3**.
4. Re-run the symlink loop from step 2.
