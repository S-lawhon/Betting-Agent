# How to Add Dashboard Users

Quick guide for managing who can log in to the betting dashboard.

- **Dashboard URL:** https://dashboard.htxtrades.org
- **Server:** `ssh root@129.212.176.202`
- Everyone uses the **same URL**; each person logs in with their **own username + password**.
- Changes take effect **immediately** — no restart needed.

---

## The easy way: the `dashboard-user` command

Run these on the server (they're on the PATH, so just call `dashboard-user ...`).

**Add a user** (or update their password if they already exist):
```bash
ssh root@129.212.176.202 "dashboard-user add <username> <password>"
```
Example:
```bash
ssh root@129.212.176.202 "dashboard-user add jordan a-strong-password"
```

**List current users** (usernames only — passwords are never shown):
```bash
ssh root@129.212.176.202 "dashboard-user list"
```

**Remove a user:**
```bash
ssh root@129.212.176.202 "dashboard-user remove jordan"
```

That's it. Tell the new person the URL (https://dashboard.htxtrades.org) and the username/password you set, and they can log in from any device.

---

## Rules & notes

- **One user per line.** Each person is a separate `username:password` entry.
- A **username cannot contain a `:` or spaces**. A password *can* contain `:`.
- Choose **strong passwords** — this now protects real trading data.
- Passwords take effect the moment you set them (the dashboard re-reads the file on every request).
- `dashboard-user add` on an existing user just **updates** their password.

---

## Manual method (fallback)

The logins live in `/opt/betting-pod-shop/.dashboard_auth`, one `user:password` per line.
If you ever edit it by hand, **make sure the file ends in a newline** before appending, or the
new entry will get stuck onto the previous line. Safe append:

```bash
ssh root@129.212.176.202 "printf '\nnewuser:newpassword\n' >> /opt/betting-pod-shop/.dashboard_auth"
```

(The `dashboard-user` command handles this automatically — prefer it.)

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Page says **"Dashboard auth not configured"** (503) | No users are set. Add one with `dashboard-user add`. |
| Browser keeps rejecting a correct-looking password (401) | Typo, or the user was removed. Re-add with `dashboard-user add`. |
| A user's password mysteriously stops working after editing by hand | The file was missing a trailing newline and entries ran together. Use `dashboard-user` instead of manual edits. |
| Health check works but dashboard won't load | `/health` is intentionally open (for monitoring); `/` and `/api/status` require a login. |

---

## Security notes

- Access is over **HTTPS** (Let's Encrypt certificate, auto-renewing) — safe to use and share across devices.
- Passwords are currently stored in **plaintext** in a file readable only by the service account. Fine for dashboard viewing access.
- **If you share with more than a few people** (or before scaling to serious real-money use), ask to switch to **bcrypt-hashed** passwords via Caddy — same commands, but nothing sensitive stored in plaintext.
- Keep the Cloudflare DNS record for `dashboard.htxtrades.org` set to **DNS only (grey cloud)**, or the certificate's auto-renewal will break.

---

*Last updated 2026-07-19.*
