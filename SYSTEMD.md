# systemd Notes for MQI Transfer

This document is a handoff for the next session. It explains how to run `mqi_transfer` with `systemd` so the receiver can bind to port `80` without running the process as root.

## Recommended Approach

Use `systemd` to launch `mqi_transfer.py` as a normal user and grant only the capability needed to bind privileged ports:

- `AmbientCapabilities=CAP_NET_BIND_SERVICE`
- `CapabilityBoundingSet=CAP_NET_BIND_SERVICE`

This is safer than starting the service with `sudo` because the process does not get full root privileges.

When `mqi_communicator` is launched as a separate service, set `MQI_EXTERNAL_TRANSFER_SERVICE=1` so it uses the systemd-managed transfer service instead of spawning its own receiver.

## Suggested Service File

Create `/etc/systemd/system/mqi-transfer.service`:

```ini
[Unit]
Description=MQI Transfer receiver
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=jokh38
Group=jokh38
WorkingDirectory=/home/jokh38/MOQUI_SMC/mqi_transfer/Linux
ExecStart=/usr/bin/python3 /home/jokh38/MOQUI_SMC/mqi_transfer/Linux/mqi_transfer.py
Restart=on-failure
RestartSec=3

AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

## Why This Works

`mqi_transfer.py` reads `app_config.ini` from the script directory, the current directory, or `/etc/mqi/app_config.ini`. Setting `WorkingDirectory` to `mqi_transfer/Linux` keeps the default config discovery working without extra arguments.

The process binds the configured `listen_port`, which is set to `80` in the checked-in config. `CAP_NET_BIND_SERVICE` lets a non-root process bind that port.

## Setup Steps

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mqi-transfer.service
sudo systemctl status mqi-transfer.service
```

To view logs:

```bash
journalctl -u mqi-transfer.service -f
```

## Notes For The Next Session

- If the service should use a virtualenv, change `ExecStart` to that interpreter instead of `/usr/bin/python3`.
- If you want a different non-root user, update both `User` and `Group`.
- If you later want `mqi_communicator` to own `mqi_transfer`, keep `systemd` as the outer supervisor and let the app manage only its internal subprocesses.
- Do not use `sudo python3 mqi_transfer.py` in production unless you explicitly want the whole receiver to run as root.

## Optional Hardening

If the environment allows it, consider adding:

- `ProtectSystem=strict`
- `ProtectHome=true`
- `PrivateTmp=true`
- `ReadWritePaths=/home/jokh38/MOQUI_SMC/mqi_transfer/Linux`

Add these only after confirming the log file and output paths still work.
