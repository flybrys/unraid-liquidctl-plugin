# Liquidctl Plugin for Unraid

A native Unraid plugin for liquid cooler monitoring and control, powered by [liquidctl](https://github.com/liquidctl/liquidctl).

> ⚠️ **Beta** — Currently tested on Corsair iCUE H100i Elite RGB. Other liquidctl-supported devices should work but may need configuration tweaks. [Report issues here](https://github.com/flybrys/unraid-liquidctl-plugin/issues).

## Features

- **Real-time monitoring** — coolant temperature, fan and pump RPMs, duty cycles
- **Visual fan curve editor** — drag-point editor with hysteresis to prevent fan hunting
- **Pump mode control** — switch between Quiet / Balanced / Extreme on supported devices
- **10-minute rolling charts** — temperature and speed history at a glance
- **Self-contained** — Python deps live entirely on the USB boot drive, nothing installed into Unraid's system Python
- **Clean uninstall** — removing the plugin removes everything

## Requirements

- Unraid 6.12 or later
- A liquidctl-supported cooler. See the [full device list](https://github.com/liquidctl/liquidctl#supported-devices). Confirmed working:
  - Corsair iCUE H100i Elite RGB

## Installation

### Via Community Apps (recommended once available)

*Coming soon — pending CA submission*

### Manual install

1. SSH into your Unraid server
2. Run:
   ```
   wget -O /boot/config/plugins/liquidctl.plg https://raw.githubusercontent.com/flybrys/unraid-liquidctl-plugin/main/liquidctl.plg
   installplg /boot/config/plugins/liquidctl.plg
   ```
3. Open the Unraid web UI → Utilities → **Liquidctl**

First install takes ~1 minute as it downloads liquidctl and dependencies. Subsequent reboots are fast — everything is cached on the USB boot drive.

## Configuration

Default fan curve and settings are sensible for most coolers. Settings live at `/boot/config/plugins/liquidctl/settings.json` and can also be edited through the web UI.

### Multiple devices

If you have multiple liquidctl-supported devices and want this plugin to control a specific one, set `device_match` in settings.json to a substring of the device description (e.g. `"H100i Elite"`). Empty string matches the first device found.

### Channel naming

Different devices expose different channel names for their fans and pump. The defaults `fan1` and `fan2` work for the H100i Elite. If your device's `liquidctl set <channel> speed` commands fail, check what channel names liquidctl reports and update `fan1_channel` / `fan2_channel` / `pump_channel` accordingly.

### Pump mode

The H100i Elite RGB exposes pump speed control via `liquidctl initialize --pump-mode=<Quiet|Balanced|Extreme>` rather than as a settable channel. The daemon handles this automatically. If your device doesn't support `--pump-mode`, set `pump_mode_supported` to `false` in settings.json.

## Uninstall

Plugin → Liquidctl → Remove.

This removes the plugin and stops the daemon. Settings, logs, and the Python libraries at `/boot/config/plugins/liquidctl/` are preserved by default — to remove those too:

```
rm -rf /boot/config/plugins/liquidctl/
```

## Architecture

```
/boot/config/plugins/liquidctl/
├── liquidctl.plg            # plugin manifest (downloaded from GitHub)
├── settings.json            # user config (created on first run)
├── source/                  # source files cached from GitHub
│   ├── liquidctl-daemon.py
│   ├── rc.liquidctl
│   ├── liquidctl.page
│   ├── api.php
│   └── index.php
└── python-libs/             # liquidctl + deps (pip install --target)

/usr/local/bin/liquidctl-daemon.py     # deployed daemon
/usr/local/sbin/rc.liquidctl            # service script
/usr/local/emhttp/plugins/liquidctl/    # web UI

/var/run/liquidctl/
├── daemon.pid
└── status.json              # rolling 10-minute history

/var/log/
├── liquidctl.log            # daemon log
└── liquidctl-install.log    # install log (debugging)
```

The daemon runs with system Python and adds `python-libs/` to PYTHONPATH so it can import liquidctl. It polls every 2 seconds, applies fan curves with hysteresis, and writes status to a JSON file that the web UI reads. Settings changes via the UI trigger a SIGHUP for in-place reload — no daemon restart needed.

## Contributing

Issues and PRs welcome. This is early-stage software — particularly interested in hearing from people running other liquidctl-supported devices so we can validate broader compatibility.

## License

GPL-3.0 — see [LICENSE](LICENSE).

Built on [liquidctl](https://github.com/liquidctl/liquidctl) (also GPL-3.0).
