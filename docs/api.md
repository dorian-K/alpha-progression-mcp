# Alpha Progression sync protocol (notes)

How the app (observed with version 7.7) syncs your own training data, so it can be read by other tools you use.

## Overview

- User data lives in a local **PouchDB** database that syncs with a per-user **CouchDB database on IBM Cloudant**. There is no REST API for workouts: reading your data means reading that CouchDB database.
- `alphaprogression.com/api/v3/*` is only used for account actions (login, signup, password reset, subscription checks, data export by email).

## Login

`POST https://alphaprogression.com/api/v3/user-login`, form-encoded (`application/x-www-form-urlencoded`):

| Field | Value |
|---|---|
| `apiKey` | Static client key (`LOGIN_API_KEY` in `config.py`) |
| `email` | Account email |
| `password` | `sha256(password + PASSWORD_SALT)` as lowercase hex, where `PASSWORD_SALT` is a static client value (`PASSWORD_SALT` in `config.py`) |

The response body is a JSON string:

- Success: `{"name": "<couch db / user name>", "token": "<couch password>"}`
- Wrong login: `{"error": "user_not_found"}` or `{"error": "incorrect_password"}`
- Too many attempts: `{"timeout": <epoch ms>}`. The account is locked until about `timeout + 5 min`, so avoid retry loops.

The app stores `{email, name, token}` and never logs in again unless the user logs out, so the token appears to be long-lived. Only email + password accounts exist; there is no Google/Apple sign-in path in this version.

## Data (CouchDB on Cloudant)

- Base URL: `https://<account>-bluemix.cloudant.com/<name>` (`COUCH_URL` in `config.py`).
- HTTP basic auth: username = `name`, password = `token`.
- The app does `db.replicate.from(remote)` on login and then a continuous two-way `db.sync(remote)`.
- Read-only access needs only standard CouchDB reads: `GET /<db>` (info, `doc_count`), `GET /<db>/_all_docs?include_docs=true`, `GET /<db>/_changes`.

### Documents

Times are epoch milliseconds. Weights carry their own unit (`weightUnit`: `kg` or `lbs`).

| Document | Key fields |
|---|---|
| `type: "session"` | `date` (start), `completed` (end), `name`, `planId`, `week`, `deload`, `skipped`, `exercises[]` → `exerciseId`, `repsTarget`, `rirTarget`, `notes`, `sets[]` → `weight`, `weightUnit`, `reps`, `rir`, `duration`, `completed` |
| `type: "plan"` | `name`, `active`, `days[]` → `name`, `exercises[]` → `exerciseId`, `weeks[]` (set/rep targets) |
| `type: "exercise"` | A custom exercise: `name`, `equipment`, `muscle`, `secondary`, `deleted` |
| `type: "measurement"` | `_id` is `measurement-<kind>` (e.g. `measurement-bodyweight`), `measurements[]` → `date`, `value`, `unit` |
| no type | Singletons such as `settings`, `profile`, `gyms`, `stats` |

Skipped or never-started sessions have no `date`. Unfinished sets have no `completed`.

Built-in exercises are referenced as `exercise-<n>`. Their names, equipment and primary muscle are listed in `src/alpha_progression_mcp/exercises.json`; repeated names are disambiguated with the equipment, e.g. `Squats (barbell)`.

## Other endpoints (not used by the MCP)

`v3/user-signup`, `v3/user-reset`, `v3/user-resend`, `v3/fix-permissions` (called when the Couch replication returns `forbidden`), `v3/check-subscription`, `v3/set-currency`, `v3/track-*`, `v3/sharing-*`, `v4/export-data` (emails a CSV), `v3/delete-account-auto`. None of these are called by this project.
