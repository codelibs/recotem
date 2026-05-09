# Cron and systemd-timer Deployment

`recotem train` is a plain process with a well-defined exit code contract. Any scheduler that can run a command on a schedule works.

## Linux cron

Add to `/etc/cron.d/recotem` (or the user crontab via `crontab -e`):

```cron
# Run at 03:00 UTC daily. Logs to /var/log/recotem/train.log.
RECOTEM_SIGNING_KEYS=prod-2026-q2:aabbcc...
0 3 * * * recotem /usr/local/bin/recotem train /etc/recotem/recipes/my_recipe.yaml >> /var/log/recotem/train.log 2>&1
```

The `RECOTEM_SIGNING_KEYS` line sets an env var for all commands in this crontab file (cron syntax). Do not put secrets in `/etc/cron.d/` with world-readable permissions — restrict the file:

```bash
chmod 600 /etc/cron.d/recotem
chown root:root /etc/cron.d/recotem
```

A cleaner approach is to source a secrets file:

```cron
0 3 * * * recotem . /etc/recotem/secrets && /usr/local/bin/recotem train /etc/recotem/recipes/my_recipe.yaml >> /var/log/recotem/train.log 2>&1
```

```bash
# /etc/recotem/secrets — mode 600, owned by the cron user
export RECOTEM_SIGNING_KEYS="prod-2026-q2:aabbcc..."
```

## Wrapper script

For more control over retries, alerting, and log rotation, use a wrapper script:

```bash
#!/usr/bin/env bash
# /usr/local/bin/recotem-train-daily.sh
set -euo pipefail

. /etc/recotem/secrets

RECIPE=/etc/recotem/recipes/my_recipe.yaml
LOG=/var/log/recotem/train-$(date +%Y%m%d-%H%M%S).log

/usr/local/bin/recotem train "$RECIPE" 2>&1 | tee "$LOG"
EXIT=${PIPESTATUS[0]}

case $EXIT in
  0) echo "train: success" ;;
  2) echo "train: RecipeError (check recipe YAML)" >&2; exit $EXIT ;;
  3) echo "train: DataSourceError (transient?)" >&2; exit $EXIT ;;
  4) echo "train: TrainingError (data or tuning issue)" >&2; exit $EXIT ;;
  5) echo "train: ArtifactError (check RECOTEM_SIGNING_KEYS)" >&2; exit $EXIT ;;
  *) echo "train: unexpected error (exit $EXIT)" >&2; exit $EXIT ;;
esac
```

```cron
0 3 * * * recotem /usr/local/bin/recotem-train-daily.sh
```

## systemd timer

A systemd timer gives better logging (journald), dependency handling, and restart control.

```ini
# /etc/systemd/system/recotem-train.service
[Unit]
Description=Recotem daily training
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=recotem
EnvironmentFile=/etc/recotem/secrets
ExecStart=/usr/local/bin/recotem train /etc/recotem/recipes/my_recipe.yaml
StandardOutput=journal
StandardError=journal
SyslogIdentifier=recotem-train
```

```ini
# /etc/systemd/system/recotem-train.timer
[Unit]
Description=Recotem daily training timer

[Timer]
OnCalendar=*-*-* 03:00:00 UTC
Persistent=true          # run on next boot if the last run was missed

[Install]
WantedBy=timers.target
```

Enable and start:

```bash
systemctl daemon-reload
systemctl enable --now recotem-train.timer
```

Check status:

```bash
systemctl status recotem-train.timer
journalctl -u recotem-train.service -n 50
```

## Environment file

`EnvironmentFile` (systemd) or the secrets-sourcing pattern (cron) should be mode `600`, owned by the service user, and excluded from version control.

```bash
# /etc/recotem/secrets — mode 600, owner recotem
RECOTEM_SIGNING_KEYS=prod-2026-q2:aabbcc...
```

## serve as a systemd service

```ini
# /etc/systemd/system/recotem-serve.service
[Unit]
Description=Recotem serve
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=recotem
EnvironmentFile=/etc/recotem/secrets
Environment=RECOTEM_HOST=0.0.0.0
Environment=RECOTEM_PORT=8080
Environment=RECOTEM_LOG_FORMAT=json
Environment=RECOTEM_WATCH_INTERVAL=30
ExecStart=/usr/local/bin/recotem serve --recipes /etc/recotem/recipes/
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=recotem-serve

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now recotem-serve.service
```

When `recotem train` writes a new artifact, the serve process detects it at the next poll and hot-swaps — no service restart needed.

## Timezone

`cron` uses the system timezone (`/etc/localtime`); set `CRON_TZ=UTC` at the
top of the crontab if you want explicit UTC. The systemd timer above pins
`OnCalendar=… UTC` which is independent of system tz.

## Lock contention

If a cron job fires while a previous training run is still active on the same host, the second invocation acquires no lock and exits 0 (skip). This is the default and is safe for standard cron setups but means **the scheduler sees a successful run when nothing was actually trained** — point alerting at the structured `train_skipped` log line, not just the exit code, or pass `--fail-on-busy`:

```bash
recotem train --fail-on-busy /etc/recotem/recipes/my_recipe.yaml
```

Exit will be non-zero when the lock is held, which most monitoring systems treat as a failure. Pair this with a cron schedule whose interval comfortably exceeds the p99 training duration; `recotem train --quiet` log lines include the run duration for sizing.
