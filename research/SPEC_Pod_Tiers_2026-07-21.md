# Spec — Pod tiers and registry/runtime reconciliation

**Date:** 2026-07-21
**Status:** proposal, nothing implemented
**Motivating question:** should validating pods be separated from the main
allocation engine, with promotion into it once they clear their gate?

---

## 1. What actually goes wrong today

Three concrete failures, all observed on 2026-07-20/21.

**(a) A validating pod's evidence is only as safe as its process.**
P-016 runs as its own systemd unit. Across two deploys, a 429 storm, and a
misdiagnosis, it took **zero 429s and was never restarted** — its 282/500 gate
sample is intact. Every pod inside the shared 5-minute engine ate the storm,
including P-017, which is 1 tournament into an 8-tournament gate.

The asset being protected is **not money — there is none.** It is *evidence*.
A contaminated gate sample cannot be re-collected; the market has moved on.
That is the whole argument for isolation, and it is stronger than the usual
prod/staging argument because the damage is irreversible rather than merely
expensive.

**(b) Runtime state and recorded state drifted, silently.**
`manager/registry.yaml` said P-017 was "built but NOT deployed" while it was
live and had placed 38 positions. Two sources of truth; the authoritative-
looking one was wrong, and nothing detected it.

**(c) "Unvalidated" is not currently representable.**
All seven trading pods carry `stage: paper`. The field encodes lifecycle
(`paper`/`parked`/`killed`/`build`/`research`) but cannot distinguish *"is
accumulating evidence toward an open gate"* from *"has cleared its gate."*

## 2. The trap: tier cannot be derived from gate presence

The obvious shortcut — "a pod with an unmet gate is validating, everything
else is production" — is wrong here:

| Pod | Gate? | Actually |
|---|---|---|
| P-001, P-014, P-015, P-016, P-017 | yes | validating |
| P-002 | **no** | **explicitly unvalidated** — 74% void rate, registry says stop counting its P&L as evidence |
| P-006 | **no** | **known-broken** — SANITY_SKIP mapping bug, 5–11 markets/cycle skipped |

Absence of a gate currently means *either* "cleared" *or* "never gated," and
the two pods with no gate are the two least trustworthy. **Tier must be
explicit data, not inferred.** A derivation would silently promote P-002 and
P-006 into production.

## 3. Recommendation, and a correction to the original proposal

The original framing was "make the registry drive what the engine loads."
**Having read the code, I think that is the wrong direction and should not be
built.**

Reason: `registry.yaml` is maintained by hand and by agents, and it is *the
artifact that was demonstrably stale* — it claimed P-017 was undeployed while
P-017 was trading. If the registry were authoritative, that same staleness
would have **silently disabled a live pod** instead of merely misreporting it.
Making documentation load-bearing converts documentation lag into outages.

**Proposed instead:** runtime config stays authoritative; the registry gains a
tier field and a reconciliation check that fails loudly.

| Concern | Owner | Why |
|---|---|---|
| What loads | `config_multi_pod.yaml` (`pods.active`) | unchanged; the trading path never depends on the reporting layer |
| What tier each pod is | `manager/registry.yaml` (`tier:`) | new explicit field |
| Do those two agree | new reconciliation check | catches (b) in minutes instead of a day |

This gets the benefit that actually failed — drift detection — without the
coupling risk.

## 4. Design

### 4.1 `tier` field (registry.yaml, per workstream)

```yaml
- id: P-017
  stage: paper          # lifecycle — unchanged
  tier: validating      # NEW: validating | production | none
```

- `validating` — has an open pre-registered gate; evidence is being collected
  and must be protected. Implies an isolation policy (§4.2).
- `production` — gate cleared, or explicitly exempted with a recorded reason.
- `none` — not a trading pod (research/build workstreams, killed, parked).

Deliberately orthogonal to `stage`. A pod can be `stage: paper, tier:
production` (cleared its gate, still paper because nothing is live yet) — which
is the state every pod should eventually reach *before* live is even discussed.

**Initial assignment**, from evidence already in the registry:

| Pod | tier | Basis |
|---|---|---|
| P-001 | validating | gate 29/200 CLV rows |
| P-002 | validating | no gate but unvalidated — fill realism unresolved |
| P-006 | validating | known mapping bug |
| P-014 | validating | gate, inconclusive at n |
| P-015 | validating | gate 0/120, locked |
| P-016 | validating | gate 282/500 |
| P-017 | validating | gate 1/8 tournaments |

**Every pod starts `validating`. None is `production` yet** — nothing has
cleared a gate. That is an accurate picture of the fund, and worth seeing
stated plainly.

### 4.2 Isolation policy for `validating`

Not a new environment — a policy the reconciler enforces on the existing
structure:

1. **Own service where practical.** P-016 already satisfies this
   (`service: betting-live-maker`). The registry already models it via the
   `service:` field; extend that field's use rather than inventing a mechanism.
2. **Own log stream**, so a shared-log rotation bug cannot eat a gate sample
   (this class of bug has already occurred once — the CLV settlement rotation
   bug).
3. **Restart discipline**: a deploy that restarts a shared unit must report
   which validating pods it interrupted. Today that is tribal knowledge; I
   checked `deploy.sh` by hand before each deploy.

Pods co-tenanted in the 5-minute engine cannot all get their own unit
overnight. The policy's near-term value is that it makes each exception
*visible and recorded* rather than accidental.

### 4.3 Allocation envelope

`aggregate_risk` currently exposes one pool: `max_pod_exposure_pct: 0.25`,
`max_total_exposure_pct: 0.50`. A validating pod and a validated one draw from
the same envelope, which is a category error — capital backing an *experiment*
should be capped independently of capital backing a *strategy*.

Proposal: a `max_validating_exposure_pct` sub-cap, with validating pods
constrained by both it and the existing per-pod cap. Since every pod is
currently `validating`, setting it near the existing total is a no-op today —
it only starts binding as pods are promoted, which is the right time for it to
appear. **Do not tune this now**; land the field, leave it inert.

### 4.4 Reconciliation check (`manager/checks.py`)

New check, run by the existing brief:

- every `pods.active` id has a registry entry — else **error** (an unregistered
  pod is trading)
- every registry `tier: validating|production` pod is either in `pods.active`
  **or** has a `service:` — else **error** (recorded as trading, isn't)
- every registry pod with a `service:` has that unit active — else **error**
- a pod whose gate metric has crossed its threshold and is still `validating`
  → **info**: promotion is due

That last one turns promotion into a prompted decision rather than something
noticed by luck.

## 5. Hidden coupling that will bite the implementer

`pods.active` is **not** only a pod list. Two settlers are built by inspecting
it directly:

- `src/engine.py:204` — `if "P-015" in config["pods"]["active"]` → tennis settler
- `src/engine.py:221` — `if "P-017" in config["pods"]["active"]` → golf settler

Any refactor that changes how `pods.active` is computed **must** preserve these
or the settlers silently stop being constructed. This exact failure has already
happened once: per `CLAUDE.md`, P-017 originally shipped with no settler, making
`on_settlement` dead code — *"Symptom would have been silent — bets placed,
nothing resolved, `_open_count` pegged at the cap, pod mute after one
tournament."*

This is the single highest-risk detail in the whole change, and it argues
further against §3's rejected design: the more machinery between config and pod
construction, the more places this coupling can be broken without a test
noticing.

**Mitigation:** before touching pod loading, add a test asserting that the
tennis and golf settlers are constructed when their pods are active. That test
should land *first*, independently.

## 6. Explicitly not proposed

**A live-trading environment.** Nothing is close to needing it: P-016 is
282/500, P-017 1/8, P-015 0/120 and locked, P-001 29/200. No pod has cleared
any gate. The project also holds **demo Kalshi credentials only**, so a live
tier could not be exercised end-to-end — it would be designed against unknowns
and tested against nothing. Build it when the first pod actually clears a gate;
far more will be known then, including whether the promotion machinery here
survives contact.

**Separate environments for resource isolation.** Kalshi throttles per-IP.
Splitting processes does not split the quota — three units contended on one
budget all day 2026-07-21. Isolation here buys blast radius and evidence
integrity, *not* headroom. Expecting headroom from it would be the org chart
without the benefit.

## 7. Sequencing

1. Settler-construction test (§5) — standalone, no behaviour change
2. `tier:` field + initial assignment (§4.1) — data only, inert
3. Reconciliation check (§4.4) — the piece that closes the observed drift
4. Isolation policy as reported exceptions (§4.2)
5. Allocation envelope, inert (§4.3)

Steps 1–3 deliver essentially all the value and change no trading behaviour.
4–5 only start mattering when something is promoted, which has not happened
yet.

## 8. Cost

Config drift between Mac and droplet is an already-experienced failure mode,
and the manager layer only became able to see local job state accurately on
2026-07-21. Every added source of truth is another surface for the daily brief
to be confidently wrong about. That is the argument for tiers being a *field
the existing deploy path reads* rather than separate environments with separate
deploy paths — and for stopping at step 3 until a real promotion forces the
rest.
