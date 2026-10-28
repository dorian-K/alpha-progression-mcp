# Alpha Progression MCP

Let **Claude** (or any other AI assistant that supports [MCP](https://modelcontextprotocol.io)) read your **[Alpha Progression](https://alphaprogression.com) gym data**: workouts, sets, reps, weights, training plans and bodyweight.

Ask things like:

- "How did my bench press progress over the last two months?"
- "What did I train this week, and how much volume per workout?"
- "I have a long run on Saturday. When was my last heavy leg day?"

It only **reads** your data. It never changes or deletes anything, and it never uses Alpha Progression's paid (Pro) features: it only reads the workout data your account already syncs.

<!-- mcp-name: io.github.dorian-k/alpha-progression-mcp -->

> Unofficial. Not affiliated with or endorsed by Alpha Progression. It reads your data from the app's sync server, which can change at any time. Use it only with your own account.

## Setup (2 minutes)

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) installed.

**1. Log in once** in a terminal:

```sh
uvx --from git+https://github.com/dorian-k/alpha-progression-mcp alpha-progression-mcp login
```

Enter your Alpha Progression email and password. They are used once and **never saved**. Only an access token is stored, in `~/.config/alpha-progression-mcp/token.json`, readable only by you.

**2. Add it to your AI assistant.**

Claude Code:

```sh
claude mcp add alpha-progression -- uvx --from git+https://github.com/dorian-k/alpha-progression-mcp alpha-progression-mcp
```

Claude Desktop, Cursor and other clients: add this to the client's MCP config file:

```json
{
  "mcpServers": {
    "alpha-progression": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/dorian-k/alpha-progression-mcp", "alpha-progression-mcp"]
    }
  }
}
```

**3. Restart your assistant** and ask about your workouts.

## What the assistant can see

| Tool | What it returns |
|---|---|
| `get_sessions` | Your workouts, newest first: date, duration, sets, volume, and per exercise the muscle and heaviest set. Filter by date. |
| `get_session` | One workout with every set (weight, reps, RIR, notes) |
| `get_exercise_history` | How one exercise progressed over time |
| `find_exercises` | Search exercises by name or muscle, with how often and when you last did them |
| `get_plans` | Your training plans |
| `get_measurements` | Bodyweight, body fat and other measurements |
| `get_document` | Any single record, for anything the others don't cover |

## Troubleshooting

| Problem | Fix |
|---|---|
| "Not logged in" or "token was rejected" | Run the login command from step 1 again |
| "Too many login attempts" | Alpha Progression locks login for a few minutes after wrong passwords. Wait, then try once more |
| Check the connection | `uvx --from git+https://github.com/dorian-k/alpha-progression-mcp alpha-progression-mcp status` |
| Log out | Delete `~/.config/alpha-progression-mcp/token.json` |

Data is cached for 5 minutes, so a workout you just logged can take a moment to show up.

## Development

```sh
git clone https://github.com/dorian-k/alpha-progression-mcp && cd alpha-progression-mcp
uv sync
uv run pytest                                   # tests
uv run ruff check . && uv run ruff format .     # lint and format
```

How the sync protocol works is described in [`docs/api.md`](docs/api.md). If the app changes its settings, they can be overridden in `~/.config/alpha-progression-mcp/config.json` or with `ALPHA_PROGRESSION_<NAME>` environment variables (see `src/alpha_progression_mcp/config.py`).

## License

MIT, see [LICENSE](LICENSE).
