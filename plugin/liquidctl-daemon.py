#!/usr/bin/env python3
"""
Liquidctl Plugin daemon — Unraid plugin for liquid cooler monitoring and control
Polls liquidctl every 2 s, applies configurable fan curves with hysteresis,
and writes a live status + 10-minute rolling history to a JSON file.

Designed to run inside the plugin's venv at:
  /boot/config/plugins/liquidctl/venv/bin/python
"""

import json, os, re, signal, time, logging
import subprocess
from datetime import datetime

# ── Paths ─────────────────────────────────────────────────────────────────────
PLUGIN_NAME   = "liquidctl"
RUN_DIR       = f"/var/run/{PLUGIN_NAME}"
BOOT_DIR      = f"/boot/config/plugins/{PLUGIN_NAME}"
STATUS_FILE   = f"{RUN_DIR}/status.json"
SETTINGS_FILE = f"{BOOT_DIR}/settings.json"
PID_FILE      = f"{RUN_DIR}/daemon.pid"
LOG_FILE      = f"/var/log/{PLUGIN_NAME}.log"
VENV_LCTL     = f"{BOOT_DIR}/venv/bin/liquidctl"

# ── Constants ─────────────────────────────────────────────────────────────────
POLL_SECS    = 2
MAX_HISTORY  = 300   # 10 min × 2 s = 300 samples

logging.basicConfig(
    filename=LOG_FILE, level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(PLUGIN_NAME)

# ── Default settings ──────────────────────────────────────────────────────────
DEFAULTS = {
    # Device targeting — empty matches the first detected device.
    # Set to a substring of the device description to disambiguate (e.g. "H100i Elite")
    "device_match": "",

    # Pump mode — set via initialize --pump-mode for devices that support it.
    # Common values: Quiet, Balanced, Extreme, Performance (depends on device firmware)
    "pump_mode":    "Balanced",
    "pump_mode_supported": True,

    # Fan curves — piecewise linear interpolation [coolant_temp_c, duty_percent]
    "fan1_curve":   [[25, 30], [35, 50], [45, 80], [50, 100]],
    "fan2_curve":   [[25, 30], [35, 50], [45, 80], [50, 100]],

    # liquidctl channel names — adjust if your device uses different names
    "fan1_channel": "fan1",
    "fan2_channel": "fan2",
    "pump_channel": "",   # leave empty if pump is not settable as a fan-style channel

    # Deadband to prevent fan hunting (°C)
    "hysteresis":   2.0,
}

# ── Mutable state ─────────────────────────────────────────────────────────────
class _State:
    settings       = dict(DEFAULTS)
    history        = []
    last_duty      = {"fan1": -1, "fan2": -1, "pump": -1}
    last_pump_mode = None
    want_reload    = False

S = _State()


# ── Settings ──────────────────────────────────────────────────────────────────
def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            s = json.load(f)
        S.settings = {**DEFAULTS, **s}
        log.info("Settings loaded  device=%r  pump_mode=%s  hysteresis=%.1f",
                 S.settings["device_match"] or "<first>",
                 S.settings["pump_mode"], S.settings["hysteresis"])
    except FileNotFoundError:
        S.settings = dict(DEFAULTS)
        save_default_settings()
    except Exception as e:
        log.error("Settings load failed: %s — using defaults", e)
        S.settings = dict(DEFAULTS)


def save_default_settings():
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(DEFAULTS, f, indent=2)
    log.info("Default settings written to %s", SETTINGS_FILE)


# ── liquidctl wrapper (uses venv binary) ──────────────────────────────────────
def lctl(*args):
    """Run liquidctl from the plugin's venv, scoped to the configured device."""
    cmd = [VENV_LCTL]
    match = S.settings.get("device_match", "")
    if match:
        cmd += ["--match", match]
    cmd += list(args)

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
        if r.returncode != 0 and r.stderr:
            log.warning("liquidctl stderr: %s", r.stderr.strip())
        return r.stdout
    except subprocess.TimeoutExpired:
        log.error("liquidctl timed out")
        return ""
    except FileNotFoundError:
        log.error("liquidctl not found at %s — is the venv set up?", VENV_LCTL)
        return ""
    except Exception as e:
        log.error("liquidctl error: %s", e)
        return ""


# ── Status parsing ────────────────────────────────────────────────────────────
_PATTERNS = {
    "liquid_temp": (r"Liquid temperature\s+([\d.]+)", float),
    "fan1_speed":  (r"Fan 1 speed\s+(\d+)",          int),
    "fan1_duty":   (r"Fan 1 duty\s+(\d+)",            int),
    "fan2_speed":  (r"Fan 2 speed\s+(\d+)",           int),
    "fan2_duty":   (r"Fan 2 duty\s+(\d+)",            int),
    "pump_speed":  (r"Pump speed\s+(\d+)",            int),
    "pump_duty":   (r"Pump duty\s+(\d+)",             int),
}

def parse_status(raw: str) -> dict:
    data = {}
    for key, (pat, cast) in _PATTERNS.items():
        m = re.search(pat, raw)
        if m:
            data[key] = cast(m.group(1))
    return data


# ── Fan curve interpolation ───────────────────────────────────────────────────
def lerp_curve(curve: list, temp: float) -> int:
    if temp <= curve[0][0]:   return curve[0][1]
    if temp >= curve[-1][0]:  return curve[-1][1]
    for (t0, d0), (t1, d1) in zip(curve, curve[1:]):
        if t0 <= temp <= t1:
            return int(d0 + (d1 - d0) * (temp - t0) / (t1 - t0))
    return curve[-1][1]


# ── Pump mode (via initialize --pump-mode) ────────────────────────────────────
def apply_pump_mode(force: bool = False):
    if not S.settings.get("pump_mode_supported", True):
        return
    mode = S.settings.get("pump_mode")
    if not mode:
        return
    if not force and mode == S.last_pump_mode:
        return
    log.info("Setting pump mode → %s", mode)
    out = lctl("initialize", f"--pump-mode={mode}")
    if out:
        S.last_pump_mode = mode


# ── Fan control ───────────────────────────────────────────────────────────────
def apply_controls(temp: float):
    s    = S.settings
    hyst = float(s.get("hysteresis", 2.0))

    def maybe_set(key: str, channel: str, target: int):
        if not channel:
            return
        prev = S.last_duty[key]
        if prev < 0 or abs(target - prev) > hyst:
            lctl("set", channel, "speed", str(target))
            S.last_duty[key] = target
            log.debug("  %-10s → %3d%%", key, target)

    maybe_set("fan1", s["fan1_channel"], lerp_curve(s["fan1_curve"], temp))
    maybe_set("fan2", s["fan2_channel"], lerp_curve(s["fan2_curve"], temp))

    # Some devices accept pump as a settable channel; many don't.
    if s.get("pump_channel"):
        pump_curve = s.get("pump_curve") or [[25, 60], [40, 75], [50, 100]]
        maybe_set("pump", s["pump_channel"], lerp_curve(pump_curve, temp))

    apply_pump_mode()  # no-op if mode unchanged


# ── Status persistence ────────────────────────────────────────────────────────
def write_status(data: dict):
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)

    point = {"ts": datetime.now().isoformat(), **data}
    S.history.append(point)
    while len(S.history) > MAX_HISTORY:
        S.history.pop(0)

    out = {
        **data,
        "timestamp":    point["ts"],
        "pump_mode":    S.settings.get("pump_mode"),
        "device_match": S.settings.get("device_match"),
        "history":      S.history,
        "daemon_pid":   os.getpid(),
    }
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f)
    os.replace(tmp, STATUS_FILE)


# ── Signal handling ───────────────────────────────────────────────────────────
def on_sighup(sig, frame):
    S.want_reload = True


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    log.info("Liquidctl Plugin daemon starting  PID=%d", os.getpid())
    load_settings()
    signal.signal(signal.SIGHUP, on_sighup)

    # Initialize device, then apply pump mode
    out = lctl("initialize")
    if out:
        log.info("Device initialized")
    else:
        log.warning("Initialize returned no output — device may not be reachable yet")
    apply_pump_mode(force=True)

    consecutive_errors = 0

    while True:
        if S.want_reload:
            load_settings()
            S.last_duty = {"fan1": -1, "fan2": -1, "pump": -1}
            S.last_pump_mode = None
            S.want_reload = False

        try:
            raw  = lctl("status")
            data = parse_status(raw)

            if "liquid_temp" not in data:
                consecutive_errors += 1
                if consecutive_errors % 10 == 1:
                    log.warning("No liquid_temp in status output (%d consecutive)", consecutive_errors)
            else:
                consecutive_errors = 0
                apply_controls(data["liquid_temp"])
                write_status(data)

        except Exception as e:
            log.error("Poll loop error: %s", e)
            consecutive_errors += 1

        time.sleep(POLL_SECS)


if __name__ == "__main__":
    main()
