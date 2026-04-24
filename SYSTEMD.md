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

Substitute `@@MOQUI_USER@@` and `@@MOQUI_ROOT@@` with the service user
and the absolute path to your MOQUI_SMC checkout before installing.

```ini
[Unit]
Description=MQI Transfer receiver
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=@@MOQUI_USER@@
Group=@@MOQUI_USER@@
WorkingDirectory=@@MOQUI_ROOT@@/mqi_transfer/Linux
ExecStart=/usr/bin/python3 @@MOQUI_ROOT@@/mqi_transfer/Linux/mqi_transfer.py
Restart=on-failure
RestartSec=3

AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
Environment=MOQUI_SMC_ROOT=@@MOQUI_ROOT@@

[Install]
WantedBy=multi-user.target
```

A one-shot install command:

```bash
sed -e "s|@@MOQUI_USER@@|$USER|g" \
    -e "s|@@MOQUI_ROOT@@|$(cd "$(dirname "$0")/.." && pwd)|g" \
    mqi-transfer.service | sudo tee /etc/systemd/system/mqi-transfer.service
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
- `ReadWritePaths=@@MOQUI_ROOT@@/mqi_transfer/Linux`

Add these only after confirming the log file and output paths still work.
