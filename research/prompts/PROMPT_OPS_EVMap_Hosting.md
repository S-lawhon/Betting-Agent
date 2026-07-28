# PROMPT — EV-Map weather: move to the droplet, start the clock

**Loss accrues at one day per day. The 30-day clock has not started.**

## The situation

EV-Map Build 2 is dead and **not** for the assumed reason. It is not "the Mac slept": **139 of 139 cron runs failed** with `Operation not permitted` — macOS TCC denying `cron` read access under `~/Desktop`. **It fails awake too.** `cron_archive.log` does not exist at all.

Already lost, permanently: **6 days of weather paper quotes (2026-07-22 → 07-27, ~1,200/day)**, unrecoverable because the horizon is 90 days and the quotes are point-in-time. Earliest completion is **~2026-08-27 if the clock starts tonight**, and it slips a day for every day it does not.

## The decision, already made

**Move it to the droplet.** Granting cron Full Disk Access on the Mac would fix the permission but leaves the collector on a laptop that sleeps, travels, and has already demonstrated it will fail silently for 139 consecutive runs with nobody noticing. The droplet already hosts four units and has a working alerting path.

## Steps

1. Port the collector to `/opt/betting-pod-shop/` following the existing pattern. Prefer a **systemd timer** over cron — cron on this droplet has already produced one silent-failure class tonight, and a timer gives `systemctl status` a real answer.
2. **The failure must be loud.** The entire reason this went unnoticed for 139 runs is that a failing collector and an idle collector looked identical. Wire it to `manager/alert.py`: N consecutive failures alarms, and **zero rows written in a window where rows were expected alarms too.** A collector that runs successfully and collects nothing is the same outcome as one that crashes.
3. Register it in `manager/registry.yaml` so the throughput instrument sees it and can report its realised rate.
4. **Verify by data, not by exit code.** Confirm rows are actually landing — count them, print the newest timestamp, and state the row count after the first successful cycle. An exit-0 collector that writes nothing is exactly the failure you are fixing.
5. Record the new 30-day clock start in the report and in `manager/registry.yaml`, with the projected completion date derived from it.
6. Confirm the Mac-side cron entry is removed or disabled so two collectors do not race, and note what happens to the old local data.

## Also in scope, cheap

The weekly settled-market archive has missed **≥2 cycles (07-19, 07-26)**, partially recoverable inside the 90-day horizon. While you are on the droplet: check whether that job is alive, recover what the horizon still allows, and give it the same loud-failure treatment. If it is on the same Mac cron, it has the same TCC cause and the same fix.

## Stop rule

Do not redesign the EV-Map methodology. This is a hosting and alerting task. If the port surfaces a methodology problem, **report it, do not fix it.**

## Deliverable

`research/REPORT_EVMap_Hosting_2026-07-29.md`: new host, scheduling mechanism, alarm conditions, verified row counts, clock start, projected completion, and the archive job's status.
