# Fund Manager

Cross-project manager for the betting fund. Answers three questions every day:

1. **Is live production healthy?**
2. **What is waiting on me?**
3. **How close is each strategy to its decision point?**

## Why it is built this way

**The registry is the point.** Before this existed, project state lived in nine
memory files, six research directories, a `PROJECT_STATUS.md` that had been stale
since March, a config, and two crontabs on two machines that did not know about
each other. `registry.yaml` is the one place that says what every workstream is,
what its pre-registered gate is, and what it is blocked on. Everything else here
is machinery around that file.

**Alerting never touches an LLM.** Threshold checks are plain Python. A model in
the alert path costs tokens, can fail in ways that look like silence, and is
unnecessary for "is the service up". The model is for judgment, on top of facts
already collected.

**Report by exception.** Workstreams blocked on calendar time (P-015 needs ~6
months to reach n=120) are summarised in one line and otherwise stay silent until
their gate fires. A daily report that repeats unchanged gets skimmed, and then it
protects nothing.

**A cron exiting 0 is not evidence it worked.** `clv_settlement` ran daily and
wrote zero rows for 31 hours while looking perfectly healthy. Every scheduled job
is judged on output freshness, never on exit code.

## Layout

| File | Runs on | What it does |
|---|---|---|
| `registry.yaml` | — | Source of truth: workstreams, gates, blocked-on, noise suppression |
| `collect.py` | droplet | Measures the real system → `state/status.json`. No LLM, no judgment |
| `checks.py` | both | Facts → findings with severity. Deterministic |
| `alert.py` | droplet | Dispatches critical/warn findings with cooldown + dedupe |
| `notify.py` | both | Email (SMTP) and push (ntfy) adapters |
| `brief.py` | Mac | Renders the daily brief as markdown / HTML |
| `refresh.py` | Mac | Runs the remote collector over SSH and pulls the snapshot back |

Plus `/manager` and `/api/manager` routes on the existing dashboard, and the
`fund-manager` agent in `.claude/agents/`.

The recursive strategy workflow now has scoped Claude role definitions in
`.claude/agents/` and a deterministic queue worker. The worker does not generate
research or make model decisions; it validates and persists agent-produced JSON:

- `scripts/run_strategy_agents.py` consumes JSON requests from
  `data/strategy_agents/queue/<role>/`
- `scripts/strategy_agent_submit.py` writes requests into that queue
- `data/strategy_agents/registry.json` is the persisted strategy registry
- `data/strategy_agents/heartbeat.jsonl` is the service heartbeat the manager
  watches

Research discovery has a separate deterministic attention queue:

- `scripts/run_research_triage.py` ranks unreviewed assignments within daily
  packet, minute, and lane-concentration limits
- `data/research_triage/dispatches/<agent>/` contains pending specialist packets
- `research/dispositions/` is the only durable proof a packet was reviewed
- `scripts/run_research_execution.py` atomically claims, releases, and completes
  bounded packets; `data/research_execution/` preserves the attempt history
- `scripts/run_research_agent_worker.py` previews the next packet through a
  provider-neutral, budgeted invocation contract. Phase 1 defaults to dry-run,
  creates no claims, invokes no model, and writes only safe worker telemetry.
- `data/research_intake/metrics.json` measures assignment → dispatch → review →
  advancement yield, 24-hour activity, per-agent pending/overdue queue age,
  and the conservative X pilot cost

The dashboard and emailed daily brief both read that shared operations contract.
They explicitly report that dispatch means a task packet was created, a worker
claim means work started, and model invocation is a separate tracked event.
Only a durable disposition proves review; a dry-run plan, invocation, or
abandoned claim does not. This prevents a healthy queue generator from being
reported as completed research labor.

The Phase 1 worker adds a deliberately narrow automation boundary:

- `config/research_agent_runtime.yaml` contains hard time, token, output-size,
  per-task cost, and daily cost limits.
- Dry-run status records a request hash, never source text or agent prompts.
- Actual invocation requires `mode: execute`, `provider.type: command`, and an
  explicit `--execute` flag. Provider subprocesses receive only environment
  variables named in `pass_env`; betting credentials are not inherited.
- Output must include measured usage, a schema-valid disposition, and an
  agent-appropriate artifact. Invalid or over-budget output releases the claim
  and cannot count as reviewed research.
- The Phase 1 systemd unit denies network access. Wiring a provider is a
  separate Phase 2 deployment with an egress and credential review.

Safe local preview:

```bash
python3 -m scripts.run_research_agent_worker --no-write-status
```

The separate five-minute Gemini/Kalshi research collector writes public,
read-only quote snapshots under `data/gemini_crossvenue/`. Its live metrics feed
the dashboard directly and its daily cadence, matches, and settlement-policy
state flow into the same research operations email. The manager registry alerts
when those metrics are more than roughly twenty minutes stale.

## Daily use

```bash
cd "~/Desktop/Betting Fund Project"
python3 manager/refresh.py && python3 manager/brief.py
```

Or ask Claude: **"what's my status"** / **"daily brief"** → the `fund-manager`
agent runs the above and adds ranking, deltas, and judgment.

Live view: <https://dashboard.htxtrades.org/manager> (behind existing basic auth).

## Setup

### 1. Delivery credentials

Create `manager/manager.env` (chmod 600, gitignored — never commit it):

```sh
# email
MANAGER_SMTP_HOST=smtp.gmail.com
MANAGER_SMTP_PORT=587
MANAGER_SMTP_USER=samsonlawhon@gmail.com
MANAGER_SMTP_PASS=<Gmail App Password, not your account password>
MANAGER_EMAIL_TO=samsonlawhon@gmail.com

# push — pick a long unguessable topic; anyone who knows it can read your alerts
MANAGER_NTFY_TOPIC=btf-<random-string>
MANAGER_NTFY_SERVER=https://ntfy.sh
```

Gmail needs an **App Password** (Google Account → Security → 2-Step Verification
→ App passwords). Your normal password will not work over SMTP.

For push, install the **ntfy** app (iOS/Android) and subscribe to the same topic.
The topic is the only secret — treat it like a password. Self-host or use
`MANAGER_NTFY_TOKEN` if you want real auth.

Then verify — an untested alert path is an assumption, not a safety net:

```bash
python3 manager/notify.py --selftest
```

### 2. Droplet cron

```cron
*/15 * * * * cd /opt/betting-pod-shop && venv/bin/python manager/collect.py --root /opt/betting-pod-shop >> /var/log/manager_collect.log 2>&1
*/15 * * * * cd /opt/betting-pod-shop && sleep 30 && venv/bin/python manager/alert.py >> /var/log/manager_alert.log 2>&1
30  12 * * * cd /opt/betting-pod-shop && venv/bin/python manager/brief.py --email >> /var/log/manager_brief.log 2>&1
```

`12:30 UTC` = 8:30am ET. The `sleep 30` lets the collector finish first.

### 3. Git mirror (the "Work completed" section)

The daily brief reports what was actually done each day — research verdicts,
code updates — by reading git commit messages. That history lives on the Mac;
the droplet is not a git repo (deploy excludes `.git`). So the collector reads a
dedicated **read-only clone of the public repo**, which it `git fetch`es at the
top of every cycle:

```bash
git clone https://github.com/S-lawhon/Betting-Agent.git /opt/betting-agent-mirror
```

`collect.py` auto-detects it (`MIRROR_PATH`, overridable via `MANAGER_GIT_REPO`);
on the Mac it reads the working tree in place instead. Only **pushed** commits
appear — push your feature branches. The section spans all branches (`--all`),
since `main` on GitHub often lags the branch work happens on. If the clone is
missing the section renders "unavailable" rather than failing; recreate it after
a droplet rebuild. It is separate from `/opt/betting-pod-shop` and untouched by
deploy.

## Maintaining the registry

**Edit `registry.yaml` when reality changes.** The machinery is only as good as
that file. In particular:

- Finished something? Change `blocked_on: human` → `time` / `nothing`, or the
  item nags you forever.
- New workstream? Add it with its gate written down *before* you start — that is
  the whole lesson of P-013 (lost $2,094 while its criteria were still being
  decided after the fact) and why P-015's rule is locked.
- New recurring noise? Add it to `suppress:` with a reason and an expiry.

## Design rules for anything added here

1. **Never write to the trading system.** Read-only against `data/`, configs, and
   journald. The collector must not be able to cause an incident.
2. **A failed probe is a finding, not a crash.** Unmeasured must never render as
   healthy.
3. **Severity discipline.** `critical` = live production broken or a locked kill
   rule fired. If something fires daily without being actionable, demote it.
4. **Locked gates are read, not interpreted.** If the rule says NO DECISION at
   n<120, that is the answer regardless of how the numbers look.
