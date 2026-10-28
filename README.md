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
| `get_weekly_training` | Working sets and training days per muscle, per week and averaged. The main input for volume and frequency advice. |
| `get_progress` | Which exercises are improving or stalled, by muscle group, based on estimated 1RM |
| `get_sessions` | Recent workouts: duration, working sets, top set and e1RM per exercise, skipped exercises |
| `get_exercise_history` | One exercise over time, with a summary and one row per workout |
| `get_session` | One workout with every set (weight, reps, RIR, notes) |
| `find_exercises` | Search exercises by name, muscle or group, with how often and when you last did them |
| `get_plans` | Your current training plan with set and rep targets |
| `get_measurements` | Bodyweight, body fat and other measurements |
| `get_document` | Any single record, for anything the others don't cover |

### How the numbers are computed

- **Working sets** leave out warm-ups (a weighted set under 75% of that exercise's heaviest set in the workout) and stretches. Hard sets are the usual unit for training volume in research.
- **Sets per muscle** count 1 for the exercise's target muscle and 0.5 for its main secondary muscles (the first two listed for built-in exercises, all of them for your own). This "fractional" counting fit the data best in the meta-regressions on training volume by Pelland et al. (2024, SportRxiv preprint).
- **Frequency** is the number of different days a muscle was trained in a week.
- **Estimated 1RM (e1RM)** uses the Epley formula, weight × (1 + reps / 30), on sets of 12 reps or fewer. If you log RIR (reps in reserve), it is added to the reps.
- **Progress** compares the median e1RM of the first and last three workouts in the range, so one good or bad day doesn't skew it.

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
