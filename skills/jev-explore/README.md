# jev-explore

A Claude Code skill that works like the built-in **Explore** subagent, plus a ranking step: before it reads a file, it asks [Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev), TypeSafe's System One model, how relevant that file is.

## Purpose

Explore finds code by grepping, globbing and reading excerpts until it can answer. Most of its time and context goes on reading files that only *mention* the search term. Jev is a classifier, not a chat model: you give it some text and a typed question, and it returns a calibrated probability in roughly 70–500 ms for about $0.04 per million input tokens. That makes it cheap enough to score every grep hit before anything gets read.

`jev-explore` does four things:

1. Runs **inside the real Explore agent** (`context: fork` + `agent: Explore`), so it gets the same read-only tools, the same small context (no CLAUDE.md, no git status), and returns only its conclusion to the main conversation.
2. Casts a wide grep/glob net sized to the breadth you ask for (`quick`, `medium`, `very thorough`), like Explore does.
3. Pipes every candidate (`path` or `path:line`) through `jev.py rank`. That sends one parallel Jev call per candidate, asking whether the excerpt answers the query, and sorts the results by score.
4. Reads only the top-scoring candidates. In big files it uses `jev.py lines` to find the right lines first. Then it reports `file:line` evidence.

If Jev isn't configured or can't be reached, the skill notices this at preflight and runs as a plain Explore search instead. The report tells you which mode ran.

## Usage

```
/jev-explore where are Stripe webhook signatures verified
/jev-explore very thorough every place that reads the session cookie
/jev-explore quick the Prisma model for appointments
```

Claude can also pick it up on its own for "find where X is handled / defined / wired up" tasks, because the description is model-facing.

The helper also works by itself:

```bash
S=~/.claude/skills/jev-explore/scripts/jev.py
python3 $S check
grep -rn 'webhook' src | cut -d: -f1,2 | python3 $S rank "where are webhook signatures verified"
python3 $S lines "where is the retry backoff computed" src/http/client.ts
```

## Configure the Jev API key

1. **Get access and a key.** Sign in at <https://console.typesafe.ai> (Jev is in early access, so you may need to join the waitlist), then create a key at <https://console.typesafe.ai/keys>.

2. **Store the key.** `jev.py` checks `$TYPESAFE_API_KEY` first, then `~/.config/typesafe/api_key`. Pick one:

   - **Key file (recommended).** The key stays out of every process's environment:
     ```bash
     mkdir -p ~/.config/typesafe
     printf '%s' 'YOUR_KEY' > ~/.config/typesafe/api_key
     chmod 600 ~/.config/typesafe/api_key
     ```
   - **Shell profile.** Add `export TYPESAFE_API_KEY=YOUR_KEY` to `~/.zshrc`, then restart Claude Code so it inherits the variable.
   - **Claude Code settings.** Add the key to `~/.claude/settings.json`. Keep it out of any settings file you commit:
     ```json
     { "env": { "TYPESAFE_API_KEY": "YOUR_KEY" } }
     ```

3. **Verify:**
   ```bash
   python3 ~/.claude/skills/jev-explore/scripts/jev.py check
   # ok: key accepted at https://api.typesafe.ai; model=jev-latest; available: ...
   ```
   Exit code `2` means no key, a rejected key, or an unreachable API. The message tells you which.

Optional overrides:

| Variable | Default | Use |
| --- | --- | --- |
| `JEV_MODEL` | `jev-latest` | Pin a version (e.g. `jev-1.13.0`) so scores don't shift when the alias moves |
| `TYPESAFE_BASE_URL` | `https://api.typesafe.ai` | Point at another endpoint |

## Cost and limits

- Each candidate is one request carrying an excerpt of about 1–3k tokens. A `very thorough` sweep (200 candidates) costs roughly 300–600k input tokens, about **$0.01–0.03**. Output tokens are free.
- Jev 1.13 allows 80 requests/s and 100k tokens/s. `rank` uses 8 workers and retries 429/529 with backoff, honouring `retry-after`.
- Jev reads text only and is strongest in English. A score tells the agent where to look. The skill always confirms by reading the code before it reports.

## Files

| File | Role |
| --- | --- |
| `SKILL.md` | The task the forked Explore agent runs: breadth table, steps, report format |
| `scripts/jev.py` | Stdlib-only Python ≥ 3.9 client: `check`, `rank`, `lines` |
