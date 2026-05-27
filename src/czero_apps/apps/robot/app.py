from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import cairo

from czero_apps.ui.gtk import Gdk, GLib, Gtk, Pango, PangoCairo
from czero_apps.ui.ime import ImeCursor, InputMethodBridge
from czero_apps.ui.theme import load_css
from czero_apps.system.single_instance import run_single_instance


WIDTH = 320
HEIGHT = 170
APP_ID = "dev.cardputerzero.defaultapps.robot"
MAIN_H = 150
BOTTOM_H = 20

ZERO_PAPER = "#E9E4D5"
PANEL_CREAM = "#F4F0E6"
ICON_WELL = "#F8F4EA"
INK_BLACK = "#171717"
LINE_BLACK = "#2A2A2A"
MUTED_TEXT = "#6E6A61"
ACCENT_ORANGE = "#E66A2C"
OK_GREEN = "#3A7D44"
WARN_RED = "#B94A2C"
HARD_SHADOW = "#BDB5A4"
SELECT_FILL = "#FBEEDE"
TITLE_FILL = "#F0D2BD"
SOFT_LINE = "#DCD5C3"

RESULT_LINE_WIDTH = 59
RESULT_BODY_WIDTH = 55
RESULT_VISIBLE_LINES = 12
RESULT_TEXT_SIZE = 8
RESULT_LINE_STEP = 9
RESULT_FIRST_LINE_Y = 43
SMALL_SCREEN_RESPONSE_CONTRACT = """You are replying through Zero Robot on a 320x170 handheld screen.
Follow this output contract:
- Reply in the same language as the user's request.
- Answer in plain text.
- Put the direct answer first.
- Keep replies short enough for a tiny screen.
- Use at most 3 short bullets when useful.
- Avoid Markdown tables, deep nesting, headings, and long code blocks.
- If more detail is needed, give a brief summary and say what to ask next.
If a command needs user confirmation or a password, ask for it before retrying.
This contract changes only presentation, not the user's task, tools, or permissions."""

Mode = Literal["compose", "confirm_transcript", "running", "result", "error", "settings", "edit_config", "agent_prompt"]
RobotMode = Literal["SAFE", "EDIT", "FULL"]

MODE_ORDER: tuple[RobotMode, ...] = ("SAFE", "EDIT", "FULL")
MODE_TOOLS: dict[RobotMode, tuple[str, ...]] = {
    "SAFE": ("read", "grep", "find", "ls"),
    "EDIT": ("read", "grep", "find", "ls", "edit", "write"),
    "FULL": ("read", "grep", "find", "ls", "edit", "write", "bash"),
}

DEFAULT_CONFIG = {
    "mode": "FULL",
    "cwd": "home",
    "pi_bin": "pi",
    "provider": "",
    "model": "",
    "session_dir": "default",
    "persist_session": True,
    "offline": False,
    "record_seconds": 5,
    "recorder": "auto",
    "transcribe_model": "gpt-4o-mini-transcribe",
    "theme": "zero-paper-robot",
}


def config_path() -> Path:
    return Path.home() / ".config" / "cardputer-zero" / "default-apps" / "robot.json"


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        save_config(dict(DEFAULT_CONFIG))
        return dict(DEFAULT_CONFIG)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        config = dict(DEFAULT_CONFIG)
        if isinstance(loaded, dict):
            config.update(loaded)
        return config
    except Exception:
        try:
            path.replace(path.with_suffix(".json.bak"))
        except OSError:
            pass
        save_config(dict(DEFAULT_CONFIG))
        return dict(DEFAULT_CONFIG)


def save_config(config: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")


def resolve_cwd(value: str) -> Path:
    if value == "home" or not value:
        return Path.home()
    return Path(value).expanduser()


def fit_text(value: str, limit: int) -> str:
    value = value.replace("\n", " ").replace("\r", " ")
    if len(value) <= limit:
        return value
    if limit <= 2:
        return value[:limit]
    return value[: limit - 2] + ".."


def wrap_text(value: str, width: int, max_lines: int = 0) -> list[str]:
    words = value.replace("\r", "").split()
    lines: list[str] = []
    current = ""
    for word in words:
        pieces = [word[index : index + width] for index in range(0, len(word), width)] or [word]
        for piece in pieces:
            if not current:
                current = piece
            elif len(current) + 1 + len(piece) <= width:
                current += " " + piece
            else:
                lines.append(current)
                if max_lines and len(lines) >= max_lines:
                    return lines
                current = piece
    if current and (not max_lines or len(lines) < max_lines):
        lines.append(current)
    return lines or [""]


def hex_to_rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return (int(value[0:2], 16) / 255, int(value[2:4], 16) / 255, int(value[4:6], 16) / 255)


def color(ctx: cairo.Context, value: str) -> None:
    ctx.set_source_rgb(*hex_to_rgb(value))


def needs_shaped_text(value: str) -> bool:
    return any(ord(ch) > 127 for ch in value)


def pango_font(size: int, bold: bool = False) -> Pango.FontDescription:
    desc = Pango.FontDescription()
    desc.set_family("monospace, Noto Sans CJK SC, WenQuanYi Micro Hei, Sans")
    desc.set_size(size * Pango.SCALE)
    desc.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
    return desc


def shaped_text_width(ctx: cairo.Context, value: str, size: int = 8, bold: bool = False) -> int:
    layout = PangoCairo.create_layout(ctx)
    layout.set_font_description(pango_font(size, bold))
    layout.set_text(value, -1)
    width, _height = layout.get_pixel_size()
    return width


def fill(ctx: cairo.Context, x: int, y: int, w: int, h: int, value: str) -> None:
    color(ctx, value)
    ctx.rectangle(x, y, w, h)
    ctx.fill()


def stroke(ctx: cairo.Context, x: int, y: int, w: int, h: int, value: str = LINE_BLACK) -> None:
    color(ctx, value)
    ctx.set_line_width(1)
    ctx.rectangle(x + 0.5, y + 0.5, w - 1, h - 1)
    ctx.stroke()


def line(ctx: cairo.Context, x1: int, y1: int, x2: int, y2: int, value: str = LINE_BLACK) -> None:
    color(ctx, value)
    ctx.set_line_width(1)
    ctx.move_to(x1 + 0.5, y1 + 0.5)
    ctx.line_to(x2 + 0.5, y2 + 0.5)
    ctx.stroke()


def text(
    ctx: cairo.Context,
    value: str,
    x: int,
    y: int,
    fg: str = INK_BLACK,
    size: int = 8,
    bold: bool = False,
    use_pango: bool = False,
) -> None:
    color(ctx, fg)
    if use_pango or needs_shaped_text(value):
        layout = PangoCairo.create_layout(ctx)
        layout.set_font_description(pango_font(size, bold))
        layout.set_text(value, -1)
        ctx.move_to(x, y - size)
        PangoCairo.show_layout(ctx, layout)
        return
    ctx.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    ctx.set_font_size(size)
    ctx.move_to(x, y)
    ctx.show_text(value)


def text_center(ctx: cairo.Context, value: str, cx: int, y: int, fg: str = INK_BLACK, size: int = 8, bold: bool = False) -> None:
    color(ctx, fg)
    if needs_shaped_text(value):
        width = shaped_text_width(ctx, value, size, bold)
        text(ctx, value, int(cx - width / 2), y, fg, size, bold)
        return
    ctx.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    ctx.set_font_size(size)
    ext = ctx.text_extents(value)
    ctx.move_to(cx - ext.width / 2, y)
    ctx.show_text(value)


def draw_key(ctx: cairo.Context, x: int, y: int, label: str, w: int, accent_first: bool = False) -> None:
    fill(ctx, x + 1, y + 13, w, 2, HARD_SHADOW)
    fill(ctx, x, y, w, 14, ICON_WELL)
    stroke(ctx, x, y, w, 15)
    line(ctx, x + 2, y + 11, x + w - 3, y + 11)
    if accent_first and label:
        text(ctx, label[0], x + 4, y + 10, ACCENT_ORANGE, 8, True)
        if len(label) > 1:
            text(ctx, label[1:], x + 10, y + 10, INK_BLACK, 8, True)
    else:
        text(ctx, label, x + 4, y + 10, ACCENT_ORANGE if label in {"R", "M", "C", "B"} else INK_BLACK, 8, True)


@dataclass
class AgentEvent:
    kind: str
    text: str


@dataclass
class ConversationTurn:
    prompt: str
    response: str


@dataclass
class AgentUiRequest:
    id: str
    method: str
    title: str = ""
    message: str = ""
    placeholder: str = ""
    options: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SettingsItem:
    key: str
    label: str
    value: str
    kind: Literal["choice", "text", "toggle"]
    choices: list[str] = field(default_factory=list)


@dataclass
class RobotState:
    mode: Mode = "compose"
    robot_mode: RobotMode = "EDIT"
    cwd: Path = field(default_factory=Path.home)
    prompt: str = ""
    transcript: str = ""
    status: str = "READY"
    events: list[AgentEvent] = field(default_factory=list)
    turns: list[ConversationTurn] = field(default_factory=list)
    result_lines: list[str] = field(default_factory=list)
    last_message: str = ""
    error: str = ""
    scroll: int = 0
    settings_index: int = 0
    edit_key: str = ""
    edit_value: str = ""
    agent_ui: AgentUiRequest | None = None
    agent_ui_value: str = ""
    agent_ui_selected: int = 0
    started_at: float = 0.0


def clean_agent_text(value: str) -> str:
    text_value = value.replace("\r", "\n")
    cleaned_lines: list[str] = []
    for raw_line in text_value.splitlines():
        line_value = raw_line.strip()
        if not line_value:
            if cleaned_lines and cleaned_lines[-1]:
                cleaned_lines.append("")
            continue
        if line_value.startswith(("updateTheUser", "updateUser")):
            continue
        for prefix in ("updateAssistant", "update"):
            if line_value.startswith(prefix):
                line_value = line_value[len(prefix) :].strip(" :")
                break
        if line_value in {"message", "message start", "message end", "turn start", "turn end", "agent end"}:
            continue
        if line_value.startswith("message "):
            line_value = line_value[8:].strip()
        if line_value:
            cleaned_lines.append(line_value)
    while cleaned_lines and not cleaned_lines[0]:
        cleaned_lines.pop(0)
    while cleaned_lines and not cleaned_lines[-1]:
        cleaned_lines.pop()
    return "\n".join(cleaned_lines).strip()


def result_position(scroll: int, total: int) -> str:
    if total <= RESULT_VISIBLE_LINES:
        return f"{total:02d}/{total:02d}"
    first = min(scroll + 1, total)
    last = min(total, scroll + RESULT_VISIBLE_LINES)
    return f"{first:02d}-{last:02d}/{total:02d}"


def agent_ui_is_secret(request: AgentUiRequest) -> bool:
    value = f"{request.title} {request.message} {request.placeholder}".lower()
    return any(word in value for word in ("password", "passphrase", "sudo", "secret", "token", "api key"))


def agent_ui_prompt(request: AgentUiRequest) -> str:
    if request.message:
        return request.message
    if request.placeholder:
        return request.placeholder
    if request.method == "confirm":
        return "Confirm this Pi request."
    if request.method == "select":
        return "Choose an option."
    return "Enter a value for Pi."


class PiBackend:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.process: subprocess.Popen[str] | None = None
        self.pending_ui: dict[str, subprocess.Popen[str]] = {}

    def resolve_pi_bin(self) -> str | None:
        configured = str(self.config.get("pi_bin", "pi")).strip() or "pi"
        if os.path.sep in configured:
            return configured if os.access(configured, os.X_OK) else None
        found = shutil.which(configured)
        if found:
            return found
        if configured == "pi":
            for candidate in (
                "/usr/local/bin/pi",
                "/usr/bin/pi",
                str(Path.home() / ".npm-global" / "bin" / "pi"),
                str(Path.home() / ".local" / "bin" / "pi"),
            ):
                if os.access(candidate, os.X_OK):
                    return candidate
        return None

    def available(self) -> bool:
        return self.resolve_pi_bin() is not None

    def command(self, cwd: Path, robot_mode: RobotMode) -> list[str]:
        pi_bin = self.resolve_pi_bin() or str(self.config.get("pi_bin", "pi"))
        args = [
            pi_bin,
            "--mode",
            "rpc",
            "--tools",
            ",".join(MODE_TOOLS[robot_mode]),
            "--append-system-prompt",
            SMALL_SCREEN_RESPONSE_CONTRACT,
        ]
        provider = str(self.config.get("provider", "")).strip()
        model = str(self.config.get("model", "")).strip()
        if provider.lower() == "default":
            provider = ""
        if model.lower() == "default":
            model = ""
        session_dir = str(self.config.get("session_dir", "")).strip()
        persist_session = bool(self.config.get("persist_session", True))
        if provider:
            args.extend(["--provider", provider])
        if model:
            args.extend(["--model", model])
        if not persist_session:
            args.append("--no-session")
        elif session_dir and session_dir != "default":
            args.extend(["--session-dir", session_dir])
        return args

    def run(self, prompt: str, cwd: Path, robot_mode: RobotMode, events: "queue.Queue[AgentEvent]") -> None:
        if not self.available():
            events.put(AgentEvent("error", "pi agent not installed. Run sudo ./install.sh or set PI BIN in settings."))
            events.put(AgentEvent("done", ""))
            return
        if not cwd.exists():
            events.put(AgentEvent("error", f"cwd not found: {cwd}"))
            events.put(AgentEvent("done", ""))
            return

        try:
            args = self.command(cwd, robot_mode)
            events.put(AgentEvent("status", f"pi {robot_mode.lower()} started"))
            completed_normally = False
            try:
                env = dict(os.environ)
                if bool(self.config.get("offline", False)):
                    env["PI_OFFLINE"] = "1"
                proc: subprocess.Popen[str] = subprocess.Popen(
                    args,
                    cwd=str(cwd),
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.PIPE,
                    bufsize=1,
                )
                self.process = proc
            except OSError as exc:
                events.put(AgentEvent("error", str(exc)))
                events.put(AgentEvent("done", ""))
                return

            assert proc.stdout is not None
            assert proc.stdin is not None
            request = {"id": "zero-robot-1", "type": "prompt", "message": prompt}
            proc.stdin.write(json.dumps(request) + "\n")
            proc.stdin.flush()
            for line_value in proc.stdout:
                kind = self._parse_jsonl(line_value, events)
                if line_value.strip():
                    self._handle_rpc_request(line_value, proc, events)
                    if kind in {"agent_end", "done"}:
                        completed_normally = True
                        break
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            stderr = proc.stderr.read() if proc.stderr else ""
            code = proc.returncode or 0
            if code != 0 and not completed_normally:
                events.put(AgentEvent("error", stderr.strip() or f"pi exited {code}"))
            elif stderr.strip():
                events.put(AgentEvent("note", stderr.strip()))
        finally:
            self.process = None
            self.pending_ui.clear()
        events.put(AgentEvent("done", ""))

    def cancel(self) -> None:
        proc = self.process
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.write(json.dumps({"id": "zero-robot-abort", "type": "abort"}) + "\n")
                proc.stdin.flush()
        except OSError:
            pass
        try:
            proc.terminate()
        except OSError:
            pass

    def _write_ui_response(self, proc: subprocess.Popen[str], request_id: str, response: dict) -> None:
        if proc.stdin is None:
            return
        payload = {"type": "extension_ui_response", "id": request_id}
        payload.update(response)
        try:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()
        except OSError:
            return

    def respond_to_ui_request(self, request_id: str, response: dict) -> None:
        proc = self.pending_ui.pop(request_id, None)
        if proc is None:
            return
        self._write_ui_response(proc, request_id, response)

    def _handle_rpc_request(self, line_value: str, proc: subprocess.Popen[str], events: "queue.Queue[AgentEvent]") -> None:
        try:
            payload = json.loads(line_value)
        except json.JSONDecodeError:
            return
        if payload.get("type") != "extension_ui_request":
            return
        method = str(payload.get("method") or "")
        if method == "notify":
            return
        if method in {"setStatus", "setWidget", "setTitle", "set_editor_text"}:
            return
        request_id = payload.get("id")
        if not request_id or proc.stdin is None:
            return
        if method not in {"select", "confirm", "input", "editor"}:
            self._write_ui_response(proc, str(request_id), {"cancelled": True})
            return
        self.pending_ui[str(request_id)] = proc
        events.put(AgentEvent("ui_request", json.dumps(payload)))

    def _parse_jsonl(self, line_value: str, events: "queue.Queue[AgentEvent]") -> str:
        raw = line_value.strip()
        if not raw:
            return ""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            events.put(AgentEvent("status", "agent output"))
            return ""
        kind = str(payload.get("type") or payload.get("event") or "event")
        event = self._event_from_payload(kind, payload)
        if event is not None:
            text_value = event.text if event.kind == "delta" else clean_agent_text(event.text)
            if text_value:
                events.put(AgentEvent(event.kind, text_value))
        return kind

    @staticmethod
    def _event_from_payload(kind: str, payload: dict) -> AgentEvent | None:
        if kind == "response":
            if payload.get("success") is False:
                return AgentEvent("error", str(payload.get("error") or "command failed"))
            return AgentEvent("status", "accepted")

        if kind == "message_update":
            return PiBackend._message_update_event(payload)

        if kind in {"message_end", "turn_end"}:
            text_value = PiBackend._assistant_message_text(payload.get("message"))
            if text_value:
                return AgentEvent("final", text_value)
            return AgentEvent("status", "message done")

        if kind == "agent_end":
            text_value = PiBackend._last_assistant_text(payload.get("messages"))
            if text_value:
                return AgentEvent("final", text_value)
            return None

        if kind in {"agent_response", "assistant_message", "message"}:
            text_value = PiBackend._assistant_message_text(payload.get("message")) or PiBackend._assistant_message_text(payload)
            if text_value:
                return AgentEvent("final", text_value)
            return None

        if kind in {"agent_start", "turn_start", "message_start"}:
            return AgentEvent("status", "work")

        if kind in {"queue_update"}:
            return AgentEvent("status", "queued")

        if kind in {"compaction_start", "compaction_end"}:
            return AgentEvent("status", "compact")

        if kind in {"auto_retry_start", "auto_retry_end"}:
            return AgentEvent("status", "retry")

        if kind.startswith("tool_execution"):
            tool_name = payload.get("toolName") or payload.get("name") or "tool"
            return AgentEvent("status", f"tool {tool_name}")

        if kind == "extension_ui_request":
            method = str(payload.get("method") or "ui")
            if method == "notify":
                notify_type = str(payload.get("notifyType") or "info")
                if notify_type == "error":
                    return AgentEvent("error", str(payload.get("message") or "agent notification"))
            return AgentEvent("status", f"ui {method}")

        if kind in {"extension_error"} or "error" in kind:
            return AgentEvent("error", str(payload.get("error") or payload.get("errorMessage") or kind.replace("_", " ")))

        if kind in {"done"}:
            return None

        return AgentEvent("status", kind.replace("_", " "))

    @staticmethod
    def _message_update_event(payload: dict) -> AgentEvent | None:
        assistant_event = payload.get("assistantMessageEvent")
        if not isinstance(assistant_event, dict):
            return AgentEvent("status", "stream")
        event_type = str(assistant_event.get("type") or "")
        if event_type == "text_delta":
            return AgentEvent(
                "delta",
                str(
                    assistant_event.get("delta")
                    or assistant_event.get("text")
                    or assistant_event.get("content")
                    or ""
                ),
            )
        if event_type == "text_end":
            text_value = PiBackend._text_content(assistant_event.get("content"))
            if text_value:
                return AgentEvent("final", text_value)
            return AgentEvent("status", "answer")
        if event_type.startswith("thinking"):
            return AgentEvent("status", "work")
        if event_type.startswith("toolcall"):
            return AgentEvent("status", "tool")
        if event_type == "error":
            return AgentEvent("error", str(assistant_event.get("reason") or "agent error"))
        if event_type == "done":
            return AgentEvent("status", "done")
        return AgentEvent("status", "stream")

    @staticmethod
    def _last_assistant_text(messages: object) -> str:
        if not isinstance(messages, list):
            return ""
        for message in reversed(messages):
            text_value = PiBackend._assistant_message_text(message)
            if text_value:
                return text_value
        return ""

    @staticmethod
    def _assistant_message_text(message: object) -> str:
        if not isinstance(message, dict):
            return ""
        role = str(message.get("role") or "assistant")
        if role != "assistant":
            return ""
        text_value = PiBackend._text_content(message.get("content"))
        if text_value:
            return text_value
        value = message.get("text")
        return value if isinstance(value, str) else ""

    @staticmethod
    def _text_content(value: object) -> str:
        if isinstance(value, str):
            return value
        if not isinstance(value, list):
            return ""
        parts: list[str] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "")
            if item_type in {"text", "output_text"} and isinstance(item.get("text"), str):
                parts.append(str(item["text"]))
        return "\n".join(part for part in parts if part)


class SpeechBackend:
    def __init__(self, config: dict) -> None:
        self.config = config

    def record_path(self) -> Path:
        return Path("/tmp") / "zero-robot-input.wav"

    def record(self) -> tuple[bool, str, Path | None]:
        seconds = max(1, int(self.config.get("record_seconds", 5)))
        path = self.record_path()
        recorder = str(self.config.get("recorder", "auto"))
        if recorder in {"auto", "pw-record"} and shutil.which("pw-record"):
            args = ["pw-record", "--duration", str(seconds), str(path)]
        elif recorder in {"auto", "arecord"} and shutil.which("arecord"):
            args = ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-d", str(seconds), str(path)]
        else:
            return False, "recorder not available", None
        try:
            completed = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=seconds + 3)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc), None
        if completed.returncode != 0:
            return False, completed.stderr.strip() or "record failed", None
        return True, "recorded", path

    def transcribe(self, path: Path) -> tuple[bool, str]:
        if not os.environ.get("OPENAI_API_KEY"):
            return False, "OPENAI_API_KEY missing"
        try:
            from openai import OpenAI  # type: ignore
        except Exception:
            return False, "python openai package missing"
        model = str(self.config.get("transcribe_model", "gpt-4o-mini-transcribe"))
        try:
            client = OpenAI()
            with path.open("rb") as audio:
                result = client.audio.transcriptions.create(model=model, file=audio)
        except Exception as exc:
            return False, str(exc)
        text_value = getattr(result, "text", "") or ""
        return (bool(text_value.strip()), text_value.strip() or "empty transcript")

    def record_and_transcribe(self) -> tuple[bool, str]:
        ok, message, path = self.record()
        if not ok or path is None:
            return False, message
        return self.transcribe(path)


class RobotCanvas(Gtk.DrawingArea):
    def __init__(self, window: "RobotWindow") -> None:
        super().__init__()
        self.window = window
        self.set_size_request(WIDTH, HEIGHT)
        self.set_focusable(True)
        self.set_draw_func(self.draw)

    def draw(self, _area, ctx: cairo.Context, _width: int, _height: int) -> None:
        fill(ctx, 0, 0, WIDTH, HEIGHT, ZERO_PAPER)
        self.draw_panel(ctx)
        self.draw_bottom(ctx)

    def draw_panel(self, ctx: cairo.Context) -> None:
        state = self.window.state
        if state.mode == "result":
            fill(ctx, 0, 0, WIDTH, MAIN_H, PANEL_CREAM)
            fill(ctx, 0, 0, WIDTH, 18, TITLE_FILL)
            line(ctx, 0, 18, WIDTH, 18)
        else:
            fill(ctx, 5, 5, WIDTH - 10, MAIN_H - 9, HARD_SHADOW)
            fill(ctx, 4, 4, WIDTH - 10, MAIN_H - 9, PANEL_CREAM)
            stroke(ctx, 4, 4, WIDTH - 10, MAIN_H - 9)
            fill(ctx, 5, 5, WIDTH - 12, 18, TITLE_FILL)
            line(ctx, 4, 23, WIDTH - 7, 23)
        if state.mode == "result":
            text(ctx, "ROBOT", 8, 13, INK_BLACK, 8, True)
            self.draw_mode_badge(ctx, 58, 4, state.robot_mode, compact=True)
            text(ctx, fit_text(str(state.cwd), 27), 104, 13, MUTED_TEXT, 7)
            text(ctx, state.status, 260, 13, MUTED_TEXT, 7, True)
        else:
            text(ctx, "ROBOT", 11, 17, INK_BLACK, 10, True)
            self.draw_mode_badge(ctx, 64, 8, state.robot_mode)
            text(ctx, fit_text(str(state.cwd), 26), 111, 17, MUTED_TEXT, 8)
            text(ctx, state.status, 257, 17, ACCENT_ORANGE if state.mode == "running" else MUTED_TEXT, 8, True)

        if state.mode == "settings":
            self.draw_settings(ctx)
        elif state.mode == "edit_config":
            self.draw_edit_config(ctx)
        elif state.mode == "confirm_transcript":
            self.draw_transcript(ctx)
        elif state.mode == "running":
            self.draw_running(ctx)
        elif state.mode == "agent_prompt":
            self.draw_agent_prompt(ctx)
        elif state.mode == "result":
            self.draw_result(ctx)
        elif state.mode == "error":
            self.draw_error(ctx)
        else:
            self.draw_compose(ctx)

    def draw_mode_badge(self, ctx: cairo.Context, x: int, y: int, value: RobotMode, compact: bool = False) -> None:
        w = 38 if compact else 39
        h = 10 if compact else 12
        fill(ctx, x, y, w, h, ICON_WELL)
        stroke(ctx, x, y, w, h, ACCENT_ORANGE if value != "SAFE" else LINE_BLACK)
        text(ctx, value, x + 5, y + (8 if compact else 9), ACCENT_ORANGE if value != "SAFE" else INK_BLACK, 6 if compact else 7, True)

    def draw_compose(self, ctx: cairo.Context) -> None:
        state = self.window.state
        text(ctx, "PROMPT", 12, 37, MUTED_TEXT, 8, True)
        fill(ctx, 11, 42, 298, 47, ICON_WELL)
        stroke(ctx, 11, 42, 298, 47, ACCENT_ORANGE)
        shown_prompt = state.prompt + self.window.im_preedit
        lines = wrap_text(shown_prompt or "Type text, or press R to record voice.", 43, 4)
        for i, line_value in enumerate(lines):
            text(ctx, ("> " if i == 0 else "  ") + line_value, 17, 56 + i * 10, INK_BLACK if shown_prompt else MUTED_TEXT, 8)
        if shown_prompt:
            text(ctx, "_", 21 + min(len(shown_prompt), 41) * 5, 56 + (len(lines) - 1) * 10, ACCENT_ORANGE, 8, True)

        tools = ",".join(MODE_TOOLS[state.robot_mode])
        text(ctx, "PI AGENT", 12, 103, MUTED_TEXT, 8, True)
        text(ctx, f"tools={fit_text(tools, 31)}", 13, 117, INK_BLACK, 8)
        model = str(self.window.config.get("model", "")).strip() or "default model"
        text(ctx, fit_text(model, 29), 13, 130, MUTED_TEXT, 8)
        text(ctx, "ENTER sends to pi rpc", 166, 130, MUTED_TEXT, 8)

    def draw_transcript(self, ctx: cairo.Context) -> None:
        state = self.window.state
        text(ctx, "TRANSCRIPT", 12, 39, MUTED_TEXT, 8, True)
        fill(ctx, 11, 45, 298, 65, ICON_WELL)
        stroke(ctx, 11, 45, 298, 65, ACCENT_ORANGE)
        for i, line_value in enumerate(wrap_text(state.transcript, 43, 5)):
            text(ctx, line_value, 17, 59 + i * 10, INK_BLACK, 8)
        text(ctx, "ENTER use transcript", 17, 128, OK_GREEN, 8, True)
        text(ctx, "C cancel", 190, 128, MUTED_TEXT, 8)

    def draw_running(self, ctx: cairo.Context) -> None:
        state = self.window.state
        elapsed = max(0, int(time.time() - state.started_at))
        text(ctx, f"RUNNING {elapsed}s", 12, 39, ACCENT_ORANGE, 8, True)
        fill(ctx, 11, 45, 298, 86, ICON_WELL)
        stroke(ctx, 11, 45, 298, 86)
        last_status = next((event.text for event in reversed(state.events) if event.kind == "status"), "work")
        preview = clean_agent_text(state.last_message)
        text(ctx, fit_text(last_status.upper(), 34), 17, 60, ACCENT_ORANGE, 8, True)
        text(ctx, "The agent is working.", 17, 76, MUTED_TEXT, 8)
        text(ctx, "Waiting for final answer.", 17, 89, MUTED_TEXT, 8)
        if preview:
            text(ctx, "Answer preview", 17, 108, OK_GREEN, 8, True)
            text(ctx, fit_text(preview.replace("\n", " "), 45), 17, 121, INK_BLACK, 8)
        else:
            text(ctx, "Final answer will appear here.", 17, 121, MUTED_TEXT, 8)

    def draw_agent_prompt(self, ctx: cairo.Context) -> None:
        state = self.window.state
        request = state.agent_ui
        if request is None:
            text(ctx, "PI REQUEST", 12, 39, MUTED_TEXT, 8, True)
            text(ctx, "Waiting for Pi.", 17, 61, MUTED_TEXT, 8)
            return
        title = request.title or request.method.upper()
        text(ctx, fit_text(title.upper(), 32), 12, 39, ACCENT_ORANGE, 8, True)
        prompt = agent_ui_prompt(request)
        fill(ctx, 11, 45, 298, 52, ICON_WELL)
        stroke(ctx, 11, 45, 298, 52, ACCENT_ORANGE)
        for i, line_value in enumerate(wrap_text(prompt, 43, 4)):
            text(ctx, line_value, 17, 59 + i * 10, INK_BLACK, 8)
        if request.method == "select":
            options = request.options or ["OK"]
            selected = max(0, min(state.agent_ui_selected, len(options) - 1))
            text(ctx, fit_text(options[selected], 39), 17, 115, OK_GREEN, 8, True)
            if len(options) > 1:
                text(ctx, f"{selected + 1}/{len(options)}", 256, 115, MUTED_TEXT, 8, True)
        elif request.method == "confirm":
            text(ctx, "ENTER confirm", 17, 115, OK_GREEN, 8, True)
            text(ctx, "C cancel", 166, 115, MUTED_TEXT, 8)
        else:
            shown_value = state.agent_ui_value + self.window.im_preedit
            if agent_ui_is_secret(request) and shown_value:
                shown_value = "*" * len(shown_value)
            for i, line_value in enumerate(wrap_text(shown_value or "Type value.", 43, 2)):
                text(ctx, ("> " if i == 0 else "  ") + line_value, 17, 112 + i * 10, INK_BLACK if shown_value else MUTED_TEXT, 8)
        text(ctx, "ENT send", 17, 134, OK_GREEN, 8, True)
        text(ctx, "C cancel", 159, 134, MUTED_TEXT, 8)

    def draw_result(self, ctx: cairo.Context) -> None:
        state = self.window.state
        text(ctx, "CONVERSATION", 8, 29, OK_GREEN, 7, True)
        text(ctx, result_position(state.scroll, len(state.result_lines)), 263, 29, MUTED_TEXT, 7, True)
        line(ctx, 0, 33, WIDTH, 33, SOFT_LINE)
        lines = state.result_lines[state.scroll : state.scroll + RESULT_VISIBLE_LINES]
        for i, line_value in enumerate(lines):
            y = RESULT_FIRST_LINE_Y + i * RESULT_LINE_STEP
            if line_value.startswith("YOU "):
                text(ctx, "YOU", 8, y, ACCENT_ORANGE, RESULT_TEXT_SIZE, True, use_pango=True)
                text(ctx, fit_text(line_value[4:].strip(), RESULT_BODY_WIDTH), 31, y, INK_BLACK, RESULT_TEXT_SIZE, use_pango=True)
            elif line_value.startswith("PI "):
                text(ctx, "PI", 8, y, OK_GREEN, RESULT_TEXT_SIZE, True, use_pango=True)
                text(ctx, fit_text(line_value[3:].strip(), RESULT_BODY_WIDTH), 31, y, INK_BLACK, RESULT_TEXT_SIZE, use_pango=True)
            elif not line_value:
                line(ctx, 8, y - 4, 312, y - 4, SOFT_LINE)
            else:
                text(ctx, fit_text(line_value, RESULT_LINE_WIDTH), 31, y, INK_BLACK, RESULT_TEXT_SIZE, use_pango=True)

    def draw_error(self, ctx: cairo.Context) -> None:
        state = self.window.state
        text(ctx, "ERROR", 12, 39, WARN_RED, 8, True)
        fill(ctx, 11, 45, 298, 66, ICON_WELL)
        stroke(ctx, 11, 45, 298, 66, WARN_RED)
        for i, line_value in enumerate(wrap_text(state.error, 43, 5)):
            text(ctx, line_value, 17, 59 + i * 10, WARN_RED if i == 0 else INK_BLACK, 8)
        text(ctx, "C clears error", 17, 128, MUTED_TEXT, 8)

    def draw_settings(self, ctx: cairo.Context) -> None:
        state = self.window.state
        items = self.window.settings_items()
        text(ctx, "ROBOT SETTINGS", 12, 39, MUTED_TEXT, 8, True)
        start = max(0, min(state.settings_index - 3, max(0, len(items) - 5)))
        for row, item in enumerate(items[start : start + 5]):
            index = start + row
            y = 45 + row * 17
            selected = index == state.settings_index
            fill(ctx, 14, y, 292, 15, SELECT_FILL if selected else PANEL_CREAM)
            stroke(ctx, 14, y, 292, 15, ACCENT_ORANGE if selected else "#DCD5C3")
            text(ctx, item.label, 20, y + 11, ACCENT_ORANGE if selected else INK_BLACK, 8, True)
            text(ctx, fit_text(item.value, 23), 116, y + 11, MUTED_TEXT, 8)
            text(ctx, ">", 294, y + 11, MUTED_TEXT, 8, True)

    def draw_edit_config(self, ctx: cairo.Context) -> None:
        item = self.window.current_settings_item()
        label = item.label if item else self.window.state.edit_key.upper()
        text(ctx, fit_text(label, 28), 12, 39, MUTED_TEXT, 8, True)
        fill(ctx, 11, 45, 298, 48, ICON_WELL)
        stroke(ctx, 11, 45, 298, 48, ACCENT_ORANGE)
        shown_value = self.window.state.edit_value + self.window.im_preedit
        for i, line_value in enumerate(wrap_text(shown_value or "Type value.", 43, 4)):
            text(ctx, ("> " if i == 0 else "  ") + line_value, 17, 59 + i * 10, INK_BLACK, 8)
        text(ctx, "ENTER save", 17, 119, OK_GREEN, 8, True)
        text(ctx, "C cancel", 122, 119, MUTED_TEXT, 8)
        if item and item.key == "cwd":
            text(ctx, "home or absolute path", 17, 133, MUTED_TEXT, 8)
        elif item and item.key == "pi_bin":
            text(ctx, "command name or path", 17, 133, MUTED_TEXT, 8)

    def draw_bottom(self, ctx: cairo.Context) -> None:
        y = MAIN_H
        fill(ctx, 0, y, WIDTH, BOTTOM_H, ZERO_PAPER)
        line(ctx, 0, y, WIDTH, y)
        state = self.window.state
        if state.mode == "agent_prompt":
            draw_key(ctx, 8, y + 3, "ENT", 28)
            text(ctx, "SEND", 41, y + 13, INK_BLACK, 8, True)
            if state.agent_ui and state.agent_ui.method == "select":
                draw_key(ctx, 100, y + 3, "UP", 22)
                draw_key(ctx, 130, y + 3, "DN", 22)
            draw_key(ctx, 224, y + 3, "C", 16)
            text(ctx, "CANCEL", 245, y + 13, MUTED_TEXT, 8)
            return
        if state.mode == "running":
            draw_key(ctx, 8, y + 3, "C", 16)
            text(ctx, "CANCEL", 29, y + 13, INK_BLACK, 8, True)
            draw_key(ctx, 96, y + 3, "UP", 22)
            text(ctx, "LOG", 123, y + 13, MUTED_TEXT, 8)
            draw_key(ctx, 196, y + 3, "ENT", 28)
            text(ctx, "DETAIL", 229, y + 13, MUTED_TEXT, 8)
            return
        if state.mode in {"result", "error"}:
            draw_key(ctx, 8, y + 3, "B", 16)
            text(ctx, "BACK", 29, y + 13, INK_BLACK, 8, True)
            draw_key(ctx, 82, y + 3, "UP", 22)
            draw_key(ctx, 111, y + 3, "DN", 22)
            if state.mode == "result":
                text(ctx, result_position(state.scroll, len(state.result_lines)), 139, y + 13, MUTED_TEXT, 8, True)
            else:
                text(ctx, "SCROLL", 139, y + 13, MUTED_TEXT, 8)
            draw_key(ctx, 230, y + 3, "ENT", 28)
            text(ctx, "NEW", 263, y + 13, MUTED_TEXT, 8)
            return
        draw_key(ctx, 6, y + 3, "R", 16)
        text(ctx, "REC", 27, y + 13, INK_BLACK, 8, True)
        draw_key(ctx, 66, y + 3, "S", 16)
        text(ctx, "SET", 87, y + 13, INK_BLACK, 8, True)
        draw_key(ctx, 145, y + 3, "ENT", 28)
        text(ctx, "RUN", 178, y + 13, INK_BLACK, 8, True)
        draw_key(ctx, 238, y + 3, "C", 16)
        text(ctx, "CANCEL", 259, y + 13, MUTED_TEXT, 8)


class RobotWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="Robot")
        self.config = load_config()
        mode = str(self.config.get("mode", "EDIT"))
        robot_mode: RobotMode = mode if mode in MODE_ORDER else "EDIT"  # type: ignore[assignment]
        self.state = RobotState(robot_mode=robot_mode, cwd=resolve_cwd(str(self.config.get("cwd", "home"))))
        self.agent = PiBackend(self.config)
        self.speech = SpeechBackend(self.config)
        self.event_queue: queue.Queue[AgentEvent] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.timeout_id = 0
        self.shutting_down = False
        self.canvas = RobotCanvas(self)
        self.im_preedit = ""
        self.ime = InputMethodBridge(
            self.canvas,
            self.ime_text,
            self.ime_cursor,
            self.on_ime_commit,
            self.on_ime_preedit,
        )
        self.set_default_size(WIDTH, HEIGHT)
        self.set_size_request(WIDTH, HEIGHT)
        self.set_resizable(False)
        self.set_decorated(False)
        self.set_child(self.canvas)
        self.connect("close-request", self.on_close_request)

        controller = Gtk.EventControllerKey()
        controller.connect("key-pressed", self.on_key)
        self.add_controller(controller)
        self.timeout_id = GLib.timeout_add(120, self.drain_events)

    def on_close_request(self, *_args) -> bool:
        self.shutdown()
        return True

    def shutdown(self) -> None:
        if self.shutting_down:
            return
        self.shutting_down = True
        if self.timeout_id:
            GLib.source_remove(self.timeout_id)
            self.timeout_id = 0
        self.agent.cancel()
        self.ime.focus_out()
        app = self.get_application()
        if app is not None:
            app.quit()

    def on_ime_commit(self, text_value: str) -> None:
        if not text_value:
            return
        if self.state.mode == "compose":
            self.state.prompt += text_value
        elif self.state.mode == "edit_config":
            self.state.edit_value += text_value
        elif self.state.mode == "agent_prompt":
            request = self.state.agent_ui
            if request is None or request.method not in {"input", "editor"}:
                return
            self.state.agent_ui_value += text_value
        else:
            return
        self.im_preedit = ""
        self.ime.update()
        self.canvas.queue_draw()

    def on_ime_preedit(self, preedit: str) -> None:
        self.im_preedit = preedit
        self.canvas.queue_draw()

    def ime_text(self) -> str:
        if self.state.mode == "compose":
            return self.state.prompt
        if self.state.mode == "edit_config":
            return self.state.edit_value
        if self.agent_prompt_accepts_text():
            return self.state.agent_ui_value
        return ""

    def ime_cursor(self) -> ImeCursor:
        text_value = self.ime_text()
        x = 21 + min(len(text_value), 41) * 5
        if self.state.mode == "agent_prompt":
            y = 108 + max(0, len(wrap_text(text_value or " ", 43, 2)) - 1) * 10
        elif self.state.mode == "edit_config":
            y = 55 + max(0, len(wrap_text(text_value or " ", 43, 4)) - 1) * 10
        else:
            y = 52 + max(0, len(wrap_text(text_value or " ", 43, 4)) - 1) * 10
        return ImeCursor(x=x, y=y)

    def agent_prompt_accepts_text(self) -> bool:
        return self.state.mode == "agent_prompt" and self.state.agent_ui is not None and self.state.agent_ui.method in {"input", "editor"}

    def settings_items(self) -> list["SettingsItem"]:
        mode_values = list(MODE_ORDER)
        return [
            SettingsItem("mode", "TOOLS", str(self.config.get("mode", "EDIT")), "choice", mode_values),
            SettingsItem("cwd", "CWD", str(self.config.get("cwd", "home")), "text"),
            SettingsItem("pi_bin", "PI BIN", str(self.config.get("pi_bin", "pi")), "text"),
            SettingsItem("provider", "PROVIDER", str(self.config.get("provider", "")) or "default", "text"),
            SettingsItem("model", "MODEL", str(self.config.get("model", "")) or "default", "text"),
            SettingsItem("session_dir", "SESSION", str(self.config.get("session_dir", "default")), "text"),
            SettingsItem("persist_session", "SESSION ON", "on" if self.config.get("persist_session", True) else "off", "toggle"),
            SettingsItem("offline", "OFFLINE", "on" if self.config.get("offline", False) else "off", "toggle"),
            SettingsItem("recorder", "RECORDER", str(self.config.get("recorder", "auto")), "choice", ["auto", "pw-record", "arecord"]),
            SettingsItem("record_seconds", "REC SECS", str(self.config.get("record_seconds", 5)), "choice", ["3", "5", "8", "10"]),
            SettingsItem(
                "transcribe_model",
                "STT MODEL",
                str(self.config.get("transcribe_model", "gpt-4o-mini-transcribe")),
                "choice",
                ["gpt-4o-mini-transcribe", "gpt-4o-transcribe"],
            ),
        ]

    def current_settings_item(self) -> "SettingsItem | None":
        items = self.settings_items()
        if not items:
            return None
        return items[min(self.state.settings_index, len(items) - 1)]

    def on_key(self, controller, keyval: int, _keycode: int, state_flags: int) -> bool:
        ctrl = bool(state_flags & Gdk.ModifierType.CONTROL_MASK)
        if ctrl and keyval in (Gdk.KEY_q, Gdk.KEY_Q):
            self.shutdown()
            self.close()
            return True

        if (self.state.mode in {"compose", "edit_config"} or self.agent_prompt_accepts_text()) and self.ime.filter_controller_key(controller):
            return True
        if ctrl and keyval == Gdk.KEY_space:
            return True

        if self.state.mode == "settings":
            return self.on_settings_key(keyval)
        if self.state.mode == "edit_config":
            return self.on_edit_config_key(keyval)
        if self.state.mode == "agent_prompt":
            return self.on_agent_prompt_key(keyval)
        if self.state.mode == "running":
            if keyval in (Gdk.KEY_c, Gdk.KEY_C):
                self.agent.cancel()
                self.set_error("agent cancelled")
                return True
            return False
        if self.state.mode in {"result", "error"}:
            return self.on_result_key(keyval)
        if self.state.mode == "confirm_transcript":
            return self.on_transcript_key(keyval)
        return self.on_compose_key(keyval)

    def on_compose_key(self, keyval: int) -> bool:
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.run_agent()
            return True
        if keyval in (Gdk.KEY_s, Gdk.KEY_S, Gdk.KEY_m, Gdk.KEY_M):
            self.state.mode = "settings"
            self.canvas.queue_draw()
            return True
        if keyval in (Gdk.KEY_r, Gdk.KEY_R):
            self.record_voice()
            return True
        if keyval in (Gdk.KEY_c, Gdk.KEY_C):
            self.state.prompt = ""
            self.ime.reset()
            self.canvas.queue_draw()
            return True
        if keyval in (Gdk.KEY_BackSpace, Gdk.KEY_Delete):
            self.state.prompt = self.state.prompt[:-1]
            self.ime.update()
            self.canvas.queue_draw()
            return True
        if keyval == Gdk.KEY_space:
            self.state.prompt += " "
            self.canvas.queue_draw()
            return True
        char = Gdk.keyval_to_unicode(keyval)
        if char and char >= 32:
            self.state.prompt += chr(char)
            self.canvas.queue_draw()
            return True
        return False

    def on_transcript_key(self, keyval: int) -> bool:
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.state.prompt = self.state.transcript
            self.state.mode = "compose"
            self.canvas.queue_draw()
            return True
        if keyval in (Gdk.KEY_c, Gdk.KEY_C, Gdk.KEY_BackSpace, Gdk.KEY_Escape):
            self.state.mode = "compose"
            self.canvas.queue_draw()
            return True
        return False

    def on_settings_key(self, keyval: int) -> bool:
        items = self.settings_items()
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Left):
            self.state.settings_index = (self.state.settings_index - 1) % len(items)
        elif keyval in (Gdk.KEY_Down, Gdk.KEY_Right):
            self.state.settings_index = (self.state.settings_index + 1) % len(items)
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self.activate_settings_item()
        elif keyval in (Gdk.KEY_c, Gdk.KEY_C, Gdk.KEY_b, Gdk.KEY_B, Gdk.KEY_BackSpace, Gdk.KEY_Escape):
            self.state.mode = "compose"
        else:
            return False
        self.canvas.queue_draw()
        return True

    def on_edit_config_key(self, keyval: int) -> bool:
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.save_edit_value()
            self.state.mode = "settings"
            self.canvas.queue_draw()
            return True
        if keyval in (Gdk.KEY_c, Gdk.KEY_C, Gdk.KEY_Escape):
            self.state.mode = "settings"
            self.ime.reset()
            self.canvas.queue_draw()
            return True
        if keyval in (Gdk.KEY_BackSpace, Gdk.KEY_Delete):
            self.state.edit_value = self.state.edit_value[:-1]
            self.ime.update()
            self.canvas.queue_draw()
            return True
        if keyval == Gdk.KEY_space:
            self.state.edit_value += " "
            self.canvas.queue_draw()
            return True
        char = Gdk.keyval_to_unicode(keyval)
        if char and char >= 32:
            self.state.edit_value += chr(char)
            self.canvas.queue_draw()
            return True
        return False

    def on_agent_prompt_key(self, keyval: int) -> bool:
        request = self.state.agent_ui
        if request is None:
            self.state.mode = "running"
            self.canvas.queue_draw()
            return True
        if keyval in (Gdk.KEY_c, Gdk.KEY_C, Gdk.KEY_Escape):
            self.agent.respond_to_ui_request(request.id, {"cancelled": True})
            self.clear_agent_ui()
            return True
        if request.method == "select":
            count = max(1, len(request.options))
            if keyval in (Gdk.KEY_Up, Gdk.KEY_Left):
                self.state.agent_ui_selected = (self.state.agent_ui_selected - 1) % count
            elif keyval in (Gdk.KEY_Down, Gdk.KEY_Right, Gdk.KEY_space):
                self.state.agent_ui_selected = (self.state.agent_ui_selected + 1) % count
            elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                options = request.options or [""]
                selected = max(0, min(self.state.agent_ui_selected, len(options) - 1))
                self.agent.respond_to_ui_request(request.id, {"value": options[selected]})
                self.clear_agent_ui()
            else:
                return False
            self.canvas.queue_draw()
            return True
        if request.method == "confirm":
            if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
                self.agent.respond_to_ui_request(request.id, {"confirmed": True})
                self.clear_agent_ui()
                return True
            return False
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.agent.respond_to_ui_request(request.id, {"value": self.state.agent_ui_value})
            self.clear_agent_ui()
            return True
        if keyval in (Gdk.KEY_BackSpace, Gdk.KEY_Delete):
            self.state.agent_ui_value = self.state.agent_ui_value[:-1]
            self.ime.update()
            self.canvas.queue_draw()
            return True
        if keyval == Gdk.KEY_space:
            self.state.agent_ui_value += " "
            self.canvas.queue_draw()
            return True
        char = Gdk.keyval_to_unicode(keyval)
        if char and char >= 32:
            self.state.agent_ui_value += chr(char)
            self.canvas.queue_draw()
            return True
        return False

    def clear_agent_ui(self) -> None:
        self.state.agent_ui = None
        self.state.agent_ui_value = ""
        self.state.agent_ui_selected = 0
        self.state.mode = "running"
        self.state.status = "RUN"
        self.im_preedit = ""
        self.ime.reset()
        self.canvas.queue_draw()

    def activate_settings_item(self) -> None:
        item = self.current_settings_item()
        if not item:
            return
        if item.kind == "choice" and item.choices:
            current = str(self.config.get(item.key, item.value))
            try:
                index = item.choices.index(current)
            except ValueError:
                index = -1
            self.update_config(item.key, item.choices[(index + 1) % len(item.choices)])
            return
        if item.kind == "toggle":
            self.update_config(item.key, not bool(self.config.get(item.key, False)))
            return
        self.state.edit_key = item.key
        self.state.edit_value = str(self.config.get(item.key, ""))
        self.state.mode = "edit_config"

    def save_edit_value(self) -> None:
        if not self.state.edit_key:
            return
        value: object = self.state.edit_value.strip()
        if self.state.edit_key == "record_seconds":
            try:
                value = max(1, int(str(value)))
            except ValueError:
                self.set_error("record seconds must be a number")
                return
        self.update_config(self.state.edit_key, value)

    def update_config(self, key: str, value: object) -> None:
        self.config[key] = value
        save_config(self.config)
        if key == "mode":
            mode = str(value)
            if mode in MODE_ORDER:
                self.state.robot_mode = mode  # type: ignore[assignment]
        elif key == "cwd":
            self.state.cwd = resolve_cwd(str(value))
        self.agent = PiBackend(self.config)
        self.speech = SpeechBackend(self.config)

    def on_result_key(self, keyval: int) -> bool:
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.state.mode = "compose"
            self.state.prompt = ""
            self.state.scroll = max(0, len(self.state.result_lines) - RESULT_VISIBLE_LINES)
        elif keyval in (Gdk.KEY_b, Gdk.KEY_B, Gdk.KEY_BackSpace, Gdk.KEY_Escape, Gdk.KEY_c, Gdk.KEY_C):
            self.state.mode = "compose"
        elif keyval == Gdk.KEY_Up:
            self.state.scroll = max(0, self.state.scroll - 1)
        elif keyval == Gdk.KEY_Down:
            self.state.scroll = min(max(0, len(self.state.result_lines) - RESULT_VISIBLE_LINES), self.state.scroll + 1)
        else:
            return False
        self.canvas.queue_draw()
        return True

    def record_voice(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.state.mode = "running"
        self.state.status = "REC"
        self.state.events = [AgentEvent("status", "recording voice")]
        self.state.started_at = time.time()
        self.canvas.queue_draw()

        def worker() -> None:
            ok, message = self.speech.record_and_transcribe()
            if ok:
                self.event_queue.put(AgentEvent("transcript", message))
            else:
                self.event_queue.put(AgentEvent("error", message))
            self.event_queue.put(AgentEvent("done", "speech"))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def run_agent(self) -> None:
        prompt = self.state.prompt.strip()
        if not prompt:
            self.set_error("prompt is empty")
            return
        if self.worker and self.worker.is_alive():
            return
        self.state.mode = "running"
        self.state.status = "RUN"
        self.state.started_at = time.time()
        self.state.events = [AgentEvent("prompt", prompt)]
        self.state.result_lines = []
        self.state.last_message = ""
        self.state.scroll = 0
        self.state.error = ""
        self.canvas.queue_draw()

        def worker() -> None:
            self.agent.run(prompt, self.state.cwd, self.state.robot_mode, self.event_queue)

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def drain_events(self) -> bool:
        if self.shutting_down:
            return False
        changed = False
        while True:
            try:
                event = self.event_queue.get_nowait()
            except queue.Empty:
                break
            changed = True
            if event.kind == "done":
                if event.text == "speech":
                    if self.state.mode != "error":
                        self.state.mode = "confirm_transcript"
                        self.state.status = "READY"
                elif self.state.mode not in {"error", "agent_prompt"}:
                    self.state.mode = "result"
                    self.state.status = "DONE"
                    self.add_conversation_turn()
                    self.state.result_lines = self.result_lines()
                    self.state.scroll = max(0, len(self.state.result_lines) - RESULT_VISIBLE_LINES)
            elif event.kind == "transcript":
                self.state.transcript = event.text
                self.state.events.append(AgentEvent("transcript", event.text))
            elif event.kind == "final":
                self.state.last_message = event.text
                self.state.events.append(AgentEvent("status", "answer ready"))
            elif event.kind == "delta":
                self.state.last_message += event.text
            elif event.kind == "error":
                self.set_error(event.text, queue_draw=False)
            elif event.kind == "ui_request":
                self.open_agent_ui_request(event.text)
            else:
                self.state.events.append(event)
                if event.kind == "status":
                    self.state.status = fit_text(event.text.upper(), 6)
        if changed or self.state.mode == "running":
            self.canvas.queue_draw()
        return True

    def open_agent_ui_request(self, value: str) -> None:
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return
        request_id = str(payload.get("id") or "")
        method = str(payload.get("method") or "")
        if not request_id or method not in {"select", "confirm", "input", "editor"}:
            return
        options_value = payload.get("options")
        options = [str(item) for item in options_value] if isinstance(options_value, list) else []
        self.state.agent_ui = AgentUiRequest(
            id=request_id,
            method=method,
            title=str(payload.get("title") or method),
            message=str(payload.get("message") or ""),
            placeholder=str(payload.get("placeholder") or payload.get("prefill") or ""),
            options=options,
        )
        self.state.agent_ui_value = str(payload.get("prefill") or "")
        self.state.agent_ui_selected = 0
        self.state.mode = "agent_prompt"
        self.state.status = "INPUT" if method in {"input", "editor"} else "ASK"
        self.im_preedit = ""
        self.ime.update()

    def add_conversation_turn(self) -> None:
        prompt = clean_agent_text(self.state.prompt)
        response = clean_agent_text(self.state.last_message)
        if not prompt and not response:
            return
        if self.state.turns and self.state.turns[-1].prompt == prompt and self.state.turns[-1].response == response:
            return
        self.state.turns.append(ConversationTurn(prompt=prompt, response=response or "Pi agent finished."))
        if len(self.state.turns) > 20:
            self.state.turns = self.state.turns[-20:]

    def result_lines(self) -> list[str]:
        lines: list[str] = []
        turns = self.state.turns or [ConversationTurn(clean_agent_text(self.state.prompt), clean_agent_text(self.state.last_message))]
        for turn_index, turn in enumerate(turns):
            prompt = clean_agent_text(turn.prompt)
            if prompt:
                first = True
                for paragraph in prompt.splitlines():
                    for line_value in wrap_text(paragraph, RESULT_BODY_WIDTH):
                        lines.append(("YOU " if first else "   ") + line_value)
                        first = False
            message = clean_agent_text(turn.response)
            if message:
                first = True
                for paragraph in message.splitlines():
                    for line_value in wrap_text(paragraph, RESULT_BODY_WIDTH):
                        lines.append(("PI " if first else "  ") + line_value)
                        first = False
            if turn_index != len(turns) - 1:
                lines.append("")
        if lines:
            return lines[-240:]
        lines.append("PI Pi agent finished.")
        return lines[-240:]

    def set_error(self, message: str, queue_draw: bool = True) -> None:
        self.state.mode = "error"
        self.state.status = "ERROR"
        self.state.error = message
        if queue_draw:
            self.canvas.queue_draw()


class RobotApplication(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)
        self.window: RobotWindow | None = None

    def do_activate(self) -> None:
        load_css()
        if self.window is None:
            self.window = RobotWindow(self)
        self.window.present()
        GLib.idle_add(self.window.canvas.grab_focus)


def run(argv: list[str] | None = None) -> int:
    return run_single_instance(APP_ID, lambda: RobotApplication().run(argv or []))
