from __future__ import annotations

import json
import os
import platform
import pwd
import re
import socket
from dataclasses import dataclass
from pathlib import Path

from czero_apps import __version__
from czero_apps.apps.settings.model import SettingsPage, SettingRow
from czero_apps.system import command


DEFAULT_PREFS = {
    "theme": "zero-paper",
    "screen_timeout": "2min",
    "update_policy": "manual",
    "preferred_audio_backend": "auto",
    "preferred_terminal": "foot",
    "hdmi_output": True,
}

SCREEN_TIMEOUT_LABELS = {
    "30s": "30 sec",
    "1min": "1 min",
    "2min": "2 min",
    "5min": "5 min",
    "never": "Never",
}

SCREEN_TIMEOUT_OPTIONS = tuple(SCREEN_TIMEOUT_LABELS.keys())


@dataclass(frozen=True)
class CommandFeedback:
    ok: bool
    text: str


class SettingsBackend:
    def __init__(self) -> None:
        self.preferences = self.load_preferences()

    def config_path(self) -> Path:
        return Path.home() / ".config" / "cardputer-zero" / "default-apps" / "settings.json"

    def display_power_path(self) -> Path:
        return Path.home() / ".config" / "cardputer-zero" / "session" / "display-power.json"

    def load_preferences(self) -> dict:
        path = self.config_path()
        if not path.exists():
            self.save_preferences(dict(DEFAULT_PREFS))
            return dict(DEFAULT_PREFS)
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            prefs = dict(DEFAULT_PREFS)
            if isinstance(loaded, dict):
                prefs.update(loaded)
                if "screen_timeout" not in loaded and "display_sleep" in loaded:
                    prefs["screen_timeout"] = loaded["display_sleep"]
                prefs.pop("display_sleep", None)
            return prefs
        except Exception:
            backup = path.with_suffix(".json.bak")
            try:
                path.replace(backup)
            except OSError:
                pass
            self.save_preferences(dict(DEFAULT_PREFS))
            return dict(DEFAULT_PREFS)

    def save_preferences(self, data: dict | None = None) -> None:
        if data is not None:
            self.preferences = data
        self.preferences.pop("display_sleep", None)
        path = self.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.preferences, indent=2, sort_keys=True), encoding="utf-8")
        self.save_display_power()

    def save_display_power(self) -> None:
        path = self.display_power_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        value = self.preferences.get("screen_timeout", DEFAULT_PREFS["screen_timeout"])
        path.write_text(json.dumps({"screen_timeout": value}, indent=2, sort_keys=True), encoding="utf-8")

    def set_preference(self, key: str, value) -> CommandFeedback:
        if key == "display_sleep":
            key = "screen_timeout"
        self.preferences[key] = value
        self.save_preferences()
        return CommandFeedback(True, "Saved")

    def run(self, args: list[str], timeout: int = 10) -> CommandFeedback:
        result = command.run(args, timeout=timeout)
        if result.ok:
            return CommandFeedback(True, (result.stdout.strip() or "Done")[:80])
        text = result.stderr.strip() or result.stdout.strip() or "Command failed"
        if result.returncode == 127:
            text = f"{args[0]} not installed"
        elif "permission" in text.lower():
            text = "Permission denied. Authorization may be required."
        return CommandFeedback(False, text[:96])

    def hostname(self) -> str:
        result = command.run(["hostnamectl", "--static"], timeout=2)
        return result.stdout.strip() if result.ok and result.stdout.strip() else socket.gethostname()

    def language(self) -> str:
        result = command.run(["locale"], timeout=2)
        match = re.search(r"^LANG=(.+)$", result.stdout, re.MULTILINE)
        value = match.group(1) if match else os.environ.get("LANG", "unknown")
        return value.replace(".UTF-8", "")

    def keyboard_layout(self) -> str:
        result = command.run(["localectl", "status"], timeout=2)
        match = re.search(r"VC Keymap:\s*(\S+)", result.stdout)
        return match.group(1) if match else "us"

    def user_shell(self) -> str:
        try:
            return Path(pwd.getpwuid(os.getuid()).pw_shell).name
        except Exception:
            return Path(os.environ.get("SHELL", "bash")).name

    def timezone(self) -> str:
        result = command.run(["timedatectl", "show", "--property=Timezone", "--value"], timeout=2)
        return result.stdout.strip() if result.ok and result.stdout.strip() else "unknown"

    def brightness(self) -> tuple[str, int, bool]:
        if not command.available("brightnessctl"):
            return "missing", 0, True
        current = command.run(["brightnessctl", "get"], timeout=2)
        maximum = command.run(["brightnessctl", "max"], timeout=2)
        try:
            value = int(current.stdout.strip())
            max_value = int(maximum.stdout.strip())
            percent = round(value * 100 / max_value) if max_value else 0
        except ValueError:
            percent = 0
        return f"{percent}%", percent, False

    def hdmi_status(self) -> tuple[str, bool, bool]:
        if not command.available("wlr-randr") and not command.available("xrandr"):
            return "stored", bool(self.preferences.get("hdmi_output", True)), False
        return ("On" if self.preferences.get("hdmi_output", True) else "Off", bool(self.preferences.get("hdmi_output", True)), False)

    def wifi_status(self) -> tuple[str, bool, bool]:
        if not command.available("nmcli"):
            return "missing", False, True
        result = command.run(["nmcli", "radio", "wifi"], timeout=2)
        on = result.stdout.strip().lower() == "enabled"
        return ("On" if on else "Off", on, False)

    def wifi_connection(self) -> str:
        if not command.available("nmcli"):
            return "NetworkManager missing"
        result = command.run(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"], timeout=4)
        for line in result.stdout.splitlines():
            parts = line.split(":", 1)
            if len(parts) == 2 and parts[0] == "yes":
                return parts[1] or "connected"
        return "Not connected"

    def ip_address(self) -> str:
        result = command.run(["hostname", "-I"], timeout=2)
        return result.stdout.split()[0] if result.stdout.split() else "none"

    def wifi_signal(self) -> str:
        if not command.available("nmcli"):
            return "n/a"
        result = command.run(["nmcli", "-t", "-f", "active,signal", "dev", "wifi"], timeout=4)
        for line in result.stdout.splitlines():
            parts = line.split(":", 1)
            if len(parts) == 2 and parts[0] == "yes":
                return f"{parts[1]}%"
        return "n/a"

    def wifi_networks(self) -> tuple[tuple[str, str, bool], ...]:
        if not command.available("nmcli"):
            return ()
        result = command.run(["nmcli", "-t", "-f", "ssid,signal,security", "dev", "wifi", "list"], timeout=8)
        networks: list[tuple[str, str, bool]] = []
        seen: set[str] = set()
        for line in result.stdout.splitlines():
            parts = line.split(":")
            ssid = parts[0].strip() if parts else ""
            if not ssid or ssid in seen:
                continue
            seen.add(ssid)
            signal = parts[1].strip() if len(parts) > 1 else "?"
            security = ":".join(parts[2:]).strip() if len(parts) > 2 else ""
            networks.append((ssid, signal, bool(security)))
            if len(networks) >= 8:
                break
        return tuple(networks)

    def network_advanced_rows(self) -> tuple[SettingRow, ...]:
        rows: list[SettingRow] = []
        ip = command.run(["ip", "-o", "-4", "addr", "show", "scope", "global"], timeout=2)
        route = command.run(["ip", "route", "show", "default"], timeout=2)
        dns = command.run(["resolvectl", "dns"], timeout=2)
        for line in ip.stdout.splitlines()[:3]:
            parts = line.split()
            if len(parts) >= 4:
                rows.append(SettingRow(f"iface-{parts[1]}", parts[1], parts[3], "readonly"))
        gateway = route.stdout.split()[2] if len(route.stdout.split()) >= 3 else "none"
        rows.append(SettingRow("gateway", "Gateway", gateway, "readonly"))
        dns_value = " ".join(dns.stdout.split()[2:4]) if dns.stdout.split() else "auto"
        rows.append(SettingRow("dns-detail", "DNS", dns_value, "readonly"))
        return tuple(rows[:6])

    def audio_sinks(self) -> tuple[tuple[str, str], ...]:
        if command.available("pactl"):
            result = command.run(["pactl", "list", "short", "sinks"], timeout=2)
            sinks = []
            for line in result.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) > 1:
                    sinks.append((parts[1], parts[1]))
            return tuple(sinks[:6])
        return (("default", "Default Output"),)

    def audio_sources(self) -> tuple[tuple[str, str], ...]:
        if command.available("pactl"):
            result = command.run(["pactl", "list", "short", "sources"], timeout=2)
            sources = []
            for line in result.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) > 1:
                    sources.append((parts[1], parts[1]))
            return tuple(sources[:6])
        return (("default", "Default Input"),)

    def volume(self) -> tuple[str, int, bool]:
        if command.available("wpctl"):
            result = command.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], timeout=2)
            match = re.search(r"([0-9.]+)", result.stdout)
            if match:
                percent = round(float(match.group(1)) * 100)
                return f"{percent}%", percent, False
        if command.available("pactl"):
            result = command.run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"], timeout=2)
            match = re.search(r"(\d+)%", result.stdout)
            if match:
                percent = int(match.group(1))
                return f"{percent}%", percent, False
        if command.available("amixer"):
            result = command.run(["amixer", "sget", "Master"], timeout=2)
            match = re.search(r"\[(\d+)%\]", result.stdout)
            if match:
                percent = int(match.group(1))
                return f"{percent}%", percent, False
        return "audio missing", 0, True

    def mute(self) -> tuple[str, bool, bool]:
        if command.available("wpctl"):
            result = command.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], timeout=2)
            muted = "MUTED" in result.stdout.upper()
            return ("On" if muted else "Off", muted, False)
        return "Off", False, not (command.available("pactl") or command.available("amixer"))

    def audio_output(self) -> str:
        if command.available("wpctl"):
            result = command.run(["wpctl", "status"], timeout=3)
            for line in result.stdout.splitlines():
                if "*" in line and ("Audio/Sink" in line or "sink" in line.lower()):
                    return line.strip().replace("*", "").strip()[:22]
        if command.available("pactl"):
            result = command.run(["pactl", "list", "short", "sinks"], timeout=2)
            first = result.stdout.splitlines()[0].split("\t") if result.stdout.splitlines() else []
            return first[1][:22] if len(first) > 1 else "Default Sink"
        return "Default"

    def audio_input(self) -> str:
        if command.available("pactl"):
            result = command.run(["pactl", "list", "short", "sources"], timeout=2)
            first = result.stdout.splitlines()[0].split("\t") if result.stdout.splitlines() else []
            return first[1][:22] if len(first) > 1 else "Default Source"
        return "Default"

    def power_source(self) -> str:
        root = Path("/sys/class/power_supply")
        if root.exists():
            for supply in root.iterdir():
                capacity = supply / "capacity"
                if capacity.exists():
                    try:
                        value = int(capacity.read_text().strip())
                        if 0 <= value <= 100:
                            return f"{value}%"
                    except (OSError, ValueError):
                        pass
            for supply in root.iterdir():
                if supply.name.upper().startswith(("AC", "USB")):
                    return "External"
        return "External"

    def about_rows(self) -> tuple[SettingRow, ...]:
        os_name = "Unknown OS"
        path = Path("/etc/os-release")
        if path.exists():
            data = {}
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    data[key] = value.strip('"')
            os_name = data.get("PRETTY_NAME", os_name)
        session = os.environ.get("XDG_SESSION_TYPE", "wayland")
        return (
            SettingRow("device", "Device", "Cardputer Zero", "readonly"),
            SettingRow("os", "OS", os_name, "readonly"),
            SettingRow("kernel", "Kernel", platform.release(), "readonly"),
            SettingRow("arch", "Arch", platform.machine(), "readonly"),
            SettingRow("host", "Hostname", self.hostname(), "readonly"),
            SettingRow("user", "User", pwd.getpwuid(os.getuid()).pw_name, "readonly"),
            SettingRow("session", "Session", session, "readonly"),
            SettingRow("apps", "Default Apps", __version__, "readonly"),
        )

    def page(self, category: str) -> SettingsPage:
        if category == "system":
            return SettingsPage("system", (
                SettingRow("hostname", "Hostname", self.hostname(), "value", True),
                SettingRow("language", "Language", self.language(), "value", True),
                SettingRow("keyboard", "Keyboard", self.keyboard_layout(), "value", True),
                SettingRow("shell", "Shell", self.user_shell(), "readonly"),
                SettingRow("timezone", "Timezone", self.timezone(), "value", True),
                SettingRow("updates", "Updates", self.preferences.get("update_policy", "manual").title(), "value", True),
            ))
        if category == "display":
            brightness, brightness_value, brightness_disabled = self.brightness()
            hdmi, hdmi_on, hdmi_disabled = self.hdmi_status()
            timeout = SCREEN_TIMEOUT_LABELS.get(
                self.preferences.get("screen_timeout", "2min"),
                "2 min",
            )
            return SettingsPage("display", (
                SettingRow("brightness", "Brightness", brightness, "slider", False, brightness_disabled, brightness_value),
                SettingRow("theme", "Theme", "Zero Paper", "value", True),
                SettingRow("screen_timeout", "Screen Timeout", timeout, "value", True),
                SettingRow("hdmi", "HDMI Output", hdmi, "toggle", False, hdmi_disabled, toggle_on=hdmi_on),
                SettingRow("internal", "Internal Display", "Active", "readonly"),
            ))
        if category == "network":
            wifi, wifi_on, wifi_disabled = self.wifi_status()
            return SettingsPage("network", (
                SettingRow("wifi", "Wi-Fi", wifi, "toggle", False, wifi_disabled, toggle_on=wifi_on),
                SettingRow("connection", "Connection", self.wifi_connection(), "value", True, wifi_disabled),
                SettingRow("ip", "IP Address", self.ip_address(), "readonly"),
                SettingRow("signal", "Signal", self.wifi_signal(), "readonly"),
                SettingRow("dns", "DNS", "Auto", "value", True),
                SettingRow("advanced", "Advanced", "Details", "value", True),
            ))
        if category == "sound":
            volume, volume_value, volume_disabled = self.volume()
            mute, muted, mute_disabled = self.mute()
            return SettingsPage("sound", (
                SettingRow("volume", "Volume", volume, "slider", False, volume_disabled, volume_value),
                SettingRow("mute", "Mute", mute, "toggle", False, mute_disabled, toggle_on=muted),
                SettingRow("output", "Output", self.audio_output(), "value", True),
                SettingRow("input", "Input", self.audio_input(), "value", True),
                SettingRow("test", "Test Sound", "Play", "action"),
            ))
        if category == "power":
            sleep = SCREEN_TIMEOUT_LABELS.get(self.preferences.get("screen_timeout", "2min"), "2 min")
            return SettingsPage("power", (
                SettingRow("power", "Battery / Power", self.power_source(), "readonly"),
                SettingRow("display_sleep", "Display Sleep", sleep, "value", True),
                SettingRow("suspend", "Suspend", "Now", "action"),
                SettingRow("reboot", "Reboot", "Confirm", "action", True),
                SettingRow("shutdown", "Shutdown", "Confirm", "action", True),
            ))
        return SettingsPage("about", self.about_rows())

    def selector_options(self, key: str) -> tuple[str, ...]:
        options = {
            "language": ("en_US.UTF-8", "zh_CN.UTF-8", "ja_JP.UTF-8", "ko_KR.UTF-8"),
            "keyboard": ("us", "uk", "de", "fr", "jp"),
            "timezone": ("Asia/Shanghai", "Asia/Seoul", "UTC", "Europe/Berlin", "America/Los_Angeles"),
            "updates": ("manual", "on-startup"),
            "theme": ("zero-paper",),
            "screen_timeout": SCREEN_TIMEOUT_OPTIONS,
            "display_sleep": SCREEN_TIMEOUT_OPTIONS,
        }
        return options.get(key, ())

    def apply_selector(self, key: str, value: str) -> CommandFeedback:
        if key == "language":
            return self.run(["localectl", "set-locale", f"LANG={value}"])
        if key == "keyboard":
            return self.run(["localectl", "set-keymap", value])
        if key == "timezone":
            return self.run(["timedatectl", "set-timezone", value])
        if key == "updates":
            return self.set_preference("update_policy", value)
        if key == "theme":
            return self.set_preference("theme", value)
        if key in {"screen_timeout", "display_sleep"}:
            return self.set_preference("screen_timeout", value)
        return CommandFeedback(False, "Unsupported setting")

    def set_hostname(self, hostname: str) -> CommandFeedback:
        hostname = hostname.strip()
        if not hostname:
            return CommandFeedback(False, "Hostname is empty")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}", hostname):
            return CommandFeedback(False, "Invalid hostname")
        return self.run(["hostnamectl", "set-hostname", hostname])

    def connect_wifi(self, ssid: str, password: str) -> CommandFeedback:
        if not command.available("nmcli"):
            return CommandFeedback(False, "NetworkManager not available")
        args = ["nmcli", "dev", "wifi", "connect", ssid]
        if password:
            args += ["password", password]
        return self.run(args, timeout=20)

    def set_audio_device(self, kind: str, device: str) -> CommandFeedback:
        if not command.available("pactl"):
            return CommandFeedback(False, "pactl not installed")
        if kind == "output":
            return self.run(["pactl", "set-default-sink", device])
        if kind == "input":
            return self.run(["pactl", "set-default-source", device])
        return CommandFeedback(False, "Unsupported audio device")

    def toggle(self, key: str, on: bool) -> CommandFeedback:
        if key == "wifi":
            return self.run(["nmcli", "radio", "wifi", "on" if on else "off"])
        if key == "hdmi":
            return self.set_preference("hdmi_output", on)
        if key == "mute":
            if command.available("wpctl"):
                return self.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"])
            if command.available("pactl"):
                return self.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
        return CommandFeedback(False, "Backend unavailable")

    def set_slider(self, key: str, value: int) -> CommandFeedback:
        value = max(0, min(100, value))
        if key == "brightness":
            return self.run(["brightnessctl", "set", f"{value}%"])
        if key == "volume":
            if command.available("wpctl"):
                return self.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{value / 100:.2f}"])
            if command.available("pactl"):
                return self.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{value}%"])
            if command.available("amixer"):
                return self.run(["amixer", "sset", "Master", f"{value}%"])
        return CommandFeedback(False, "Backend unavailable")

    def action(self, key: str) -> CommandFeedback:
        if key == "suspend":
            return self.run(["systemctl", "suspend"])
        if key == "reboot":
            return self.run(["systemctl", "reboot"])
        if key == "shutdown":
            return self.run(["systemctl", "poweroff"])
        if key == "test":
            if command.available("speaker-test"):
                result = command.spawn(["speaker-test", "-t", "sine", "-f", "1000", "-l", "1"])
                return CommandFeedback(result.ok, "Playing" if result.ok else result.stderr or "speaker-test failed")
            wav = Path("/usr/share/sounds/alsa/Front_Center.wav")
            if command.available("aplay") and wav.exists():
                result = command.spawn(["aplay", str(wav)])
                return CommandFeedback(result.ok, "Playing" if result.ok else result.stderr or "aplay failed")
            return CommandFeedback(False, "speaker-test not installed")
        return CommandFeedback(False, "Unsupported action")
