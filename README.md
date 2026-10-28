# Alpha Progression MCP

Connect **Claude** (or any [MCP](https://modelcontextprotocol.io) client) to your **[Alpha Progression](https://alphaprogression.com)** gym data: analyse your training and adjust your plan.

- "Am I doing enough sets per week for each muscle?"
- "Which lifts have stalled in the last 3 months?"
- "Add a hamstring exercise to Day 2 and lower bench to 4 × 6."

<!-- mcp-name: io.github.dorian-K/alpha-progression-mcp -->

> Unofficial, not affiliated with Alpha Progression. Use it only with your own account.

## Setup

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/).

1. Log in once. Your email and password are not saved, only an access token (`~/.config/alpha-progression-mcp/token.json`):
   ```sh
   uvx alpha-progression-mcp login
   ```
2. Add it to Claude Code:
   ```sh
   claude mcp add alpha-progression -- uvx alpha-progression-mcp
   ```
   Other clients:
   ```json
   {
     "mcpServers": {
       "alpha-progression": {
         "command": "uvx",
         "args": ["alpha-progression-mcp"]
       }
     }
   }
   ```
3. Restart your client.

## Tools

| Tool | Returns |
|---|---|
| `get_weekly_training` | Sets and training days per muscle, per week |
| `get_progress` | Lifts improving or stalled, by muscle group (e1RM) |
| `get_sessions` / `get_session` | Recent workouts / every set of one workout |
| `get_exercise_history` | One exercise over time |
| `find_exercises` | Exercises by name or muscle |
| `get_plans` | Your current plan with set and rep targets |
| `get_measurements` | Bodyweight and other measurements |
| `get_document` | Any single record |
| `set_plan_targets` / `add_plan_exercise` / `swap_plan_exercise` / `remove_plan_exercise` | Change one thing in your plan |

**Plan editing** makes one targeted change per call, previews it first and only saves after you approve. Workout history is never touched; a backup of the plan is saved to `~/.config/alpha-progression-mcp/backups/` before each change, and it only changes what the free version of the app can (no RIR targets, deloads or periodisation). Changes appear in the app after it syncs. To turn editing off, set `ALPHA_PROGRESSION_READ_ONLY=1`.

**Names** can be partial ("bench press", "day 2"); unclear ones return suggestions.

**Metrics:** working sets exclude warm-ups (lighter sets before the first heavy set) and stretches. Sets per muscle count 1 for the target muscle and 0.5 for its main secondary muscles (fractional counting, Pelland et al. 2024). e1RM uses Epley, weight × (1 + reps / 30), up to 15 reps.

## Troubleshooting

- **"Not logged in" / "login has expired":** run the login command again.
- **"Too many login attempts":** wait a few minutes; Alpha Progression locks login after wrong passwords.
- **Check the connection:** `uvx alpha-progression-mcp status`
- **Log out:** delete `~/.config/alpha-progression-mcp/token.json`.

## Development

```sh
uv sync && uv run pytest && uv run ruff check . && uv run ruff format .
```

The sync protocol is described in [`docs/api.md`](docs/api.md). MIT license.
