#!/usr/bin/env python3
"""Generate a configurable Waybar SVG with pupils that track the Hyprland cursor."""

from __future__ import annotations

import html
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HYPRCTL_TIMEOUT_SECONDS = 0.18
WAYBAR_CONFIG_PATH = Path(os.getenv("WAYBAR_GOOGLE_EYES_WAYBAR_CONFIG", "~/.config/waybar/config.jsonc")).expanduser()
WAYBAR_DISPLAY_STATE_PATH = Path(
    os.getenv("WAYBAR_DISPLAY_STATE", "~/.config/waybar/display-state.json")
).expanduser()
USER_CONFIG_PATH = Path(os.getenv("WAYBAR_GOOGLE_EYES_CONFIG", "~/.config/waybar/googly-eyes.toml")).expanduser()
OUTPUT_PATH = Path(os.getenv("WAYBAR_GOOGLE_EYES_PATH", "~/.cache/waybar/googly-eyes.svg")).expanduser()
PID_PATH = Path(os.getenv("WAYBAR_GOOGLE_EYES_PID", "~/.cache/waybar/googly-eyes.pid")).expanduser()
BLINK_PATH = Path(os.getenv("WAYBAR_GOOGLE_EYES_BLINK", "~/.cache/waybar/googly-eyes-blink.json")).expanduser()
MONITOR_CACHE_PATH = Path(
    os.getenv("WAYBAR_GOOGLE_EYES_MONITOR_CACHE", "~/.cache/waybar/googly-eyes-monitors.json")
).expanduser()
MONITOR_CACHE_SECONDS = float(os.getenv("WAYBAR_GOOGLE_EYES_MONITOR_CACHE_SECONDS", "3"))
DAEMON_INTERVAL_SECONDS = float(os.getenv("WAYBAR_GOOGLE_EYES_INTERVAL", "0.016"))
MAX_HELD_BLINK_SECONDS = float(os.getenv("WAYBAR_GOOGLE_EYES_MAX_HELD_SECONDS", "1.5"))
WORKSPACE_POLL_SECONDS = float(os.getenv("WAYBAR_GOOGLE_EYES_WORKSPACE_POLL_SECONDS", "0.05"))

BASE_SVG_WIDTH = 92.0
BASE_SVG_HEIGHT = 40.0
BASE_LEFT_EYE = (27.0, 20.0)
BASE_RIGHT_EYE = (65.0, 20.0)
BASE_EYE_RADIUS_X = 15.4
BASE_EYE_RADIUS_Y = 17.8
BASE_PUPIL_RADIUS = 7.3
BASE_PUPIL_TRAVEL = 8.0
BASE_EYE_DISTANCE = BASE_RIGHT_EYE[0] - BASE_LEFT_EYE[0]
BASE_MARGIN_X = BASE_LEFT_EYE[0] - BASE_EYE_RADIUS_X
BASE_CENTER_Y = BASE_LEFT_EYE[1]

ANCHOR_X = os.getenv("WAYBAR_GOOGLE_EYES_ANCHOR_X", "center")
ANCHOR_Y_OFFSET = int(os.getenv("WAYBAR_GOOGLE_EYES_ANCHOR_Y", "12"))

HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


@dataclass(frozen=True)
class EyeStyle:
    enabled: bool
    sclera_color: str
    pupil_color: str
    outline_color: str
    size: float
    pupil_size: float
    blink_size: float

    @property
    def radius_x(self) -> float:
        return BASE_EYE_RADIUS_X * self.size

    @property
    def radius_y(self) -> float:
        return BASE_EYE_RADIUS_Y * self.size

    @property
    def pupil_travel(self) -> float:
        return BASE_PUPIL_TRAVEL * self.size


@dataclass(frozen=True)
class BlinkConfig:
    enabled: bool
    duration_seconds: float
    character: str


@dataclass(frozen=True)
class GooglyConfig:
    left: EyeStyle
    right: EyeStyle
    distance: float
    blink: BlinkConfig


@dataclass(frozen=True)
class LayoutEye:
    name: str
    cx: float
    cy: float
    style: EyeStyle


@dataclass(frozen=True)
class EyeLayout:
    width: int
    height: int
    eyes: tuple[LayoutEye, ...]
    max_pupil_travel: float


@dataclass(frozen=True)
class Monitor:
    x: int
    y: int
    width: int
    height: int
    scale: float = 1.0
    transform: int = 0

    @property
    def logical_width(self) -> int:
        width = self.width / self.scale
        height = self.height / self.scale
        if self.transform in {1, 3, 5, 7}:
            return round(height)
        return round(width)

    @property
    def logical_height(self) -> int:
        width = self.width / self.scale
        height = self.height / self.scale
        if self.transform in {1, 3, 5, 7}:
            return round(width)
        return round(height)

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.x + self.logical_width and self.y <= py < self.y + self.logical_height

    def anchor(self, position: str) -> tuple[float, float]:
        anchor_y = self.y + ANCHOR_Y_OFFSET if position == "top" else self.y + self.logical_height - ANCHOR_Y_OFFSET
        anchor_x = self.x + self.logical_width / 2 if ANCHOR_X == "center" else self.x + int(ANCHOR_X)
        return (anchor_x, anchor_y)


_CONFIG_CACHE: tuple[int | None, GooglyConfig] | None = None


def default_config() -> GooglyConfig:
    base = EyeStyle(
        enabled=True,
        sclera_color="#fffdf2",
        pupil_color="#111111",
        outline_color="#1d2021",
        size=1.0,
        pupil_size=BASE_PUPIL_RADIUS,
        blink_size=22.0,
    )
    return GooglyConfig(
        left=base,
        right=base,
        distance=BASE_EYE_DISTANCE,
        blink=BlinkConfig(enabled=True, duration_seconds=0.14, character="—"),
    )


def file_mtime_ns(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return None


def valid_color(value: Any, fallback: str) -> str:
    if isinstance(value, str) and HEX_COLOR_RE.fullmatch(value):
        return value
    return fallback


def valid_float(value: Any, fallback: float, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        return fallback
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(parsed) or parsed < minimum or parsed > maximum:
        return fallback
    return parsed


def valid_bool(value: Any, fallback: bool) -> bool:
    return value if isinstance(value, bool) else fallback


def table(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    return value if isinstance(value, dict) else {}


def eye_from_table(global_eye: EyeStyle, data: dict[str, Any]) -> EyeStyle:
    return EyeStyle(
        enabled=valid_bool(data.get("enabled"), global_eye.enabled),
        sclera_color=valid_color(data.get("sclera_color"), global_eye.sclera_color),
        pupil_color=valid_color(data.get("pupil_color"), global_eye.pupil_color),
        outline_color=valid_color(data.get("outline_color"), global_eye.outline_color),
        size=valid_float(data.get("size"), global_eye.size, minimum=0.25, maximum=3.0),
        pupil_size=valid_float(data.get("pupil_size"), global_eye.pupil_size, minimum=0.5, maximum=40.0),
        blink_size=valid_float(data.get("blink_size"), global_eye.blink_size, minimum=1.0, maximum=80.0),
    )


def parse_user_config(data: dict[str, Any]) -> GooglyConfig:
    defaults = default_config()
    eyes = table(data, "eyes")
    blink = table(data, "blink")

    global_eye = eye_from_table(defaults.left, eyes)
    return GooglyConfig(
        left=eye_from_table(global_eye, table(data, "left")),
        right=eye_from_table(global_eye, table(data, "right")),
        distance=valid_float(eyes.get("distance"), defaults.distance, minimum=0.0, maximum=400.0),
        blink=BlinkConfig(
            enabled=valid_bool(blink.get("enabled"), defaults.blink.enabled),
            duration_seconds=valid_float(blink.get("duration_ms"), defaults.blink.duration_seconds * 1000, minimum=0.0, maximum=5000.0) / 1000,
            character=str(blink.get("character", defaults.blink.character))[:8] or defaults.blink.character,
        ),
    )


def load_config() -> GooglyConfig:
    global _CONFIG_CACHE
    mtime = file_mtime_ns(USER_CONFIG_PATH)
    if _CONFIG_CACHE is not None and _CONFIG_CACHE[0] == mtime:
        return _CONFIG_CACHE[1]

    if mtime is None:
        config = default_config()
    else:
        try:
            with USER_CONFIG_PATH.open("rb") as handle:
                raw = tomllib.load(handle)
            config = parse_user_config(raw if isinstance(raw, dict) else {})
        except (OSError, tomllib.TOMLDecodeError):
            config = default_config()

    _CONFIG_CACHE = (mtime, config)
    return config


def eye_layout(config: GooglyConfig) -> EyeLayout:
    left = config.left
    right = config.right
    width = max(
        int(BASE_SVG_WIDTH),
        math.ceil(max(left.radius_x if left.enabled else 0, right.radius_x if right.enabled else 0) * 2 + max(2.0, BASE_MARGIN_X) * 2),
    )
    enabled: list[tuple[str, EyeStyle]] = []
    if left.enabled:
        enabled.append(("left", left))
    if right.enabled:
        enabled.append(("right", right))

    if not enabled:
        return EyeLayout(width=width, height=int(BASE_SVG_HEIGHT), eyes=(), max_pupil_travel=0.0)

    max_ry = max(style.radius_y for _, style in enabled)
    cy = max(BASE_CENTER_Y, max_ry + 2)
    height = max(int(BASE_SVG_HEIGHT), math.ceil(cy + max_ry + 2))
    margin = max(2.0, BASE_MARGIN_X)

    if len(enabled) == 1:
        name, style = enabled[0]
        eyes = (LayoutEye(name=name, cx=width / 2, cy=cy, style=style),)
    else:
        min_distance = left.radius_x + right.radius_x + 2
        max_distance = width - (2 * margin) - left.radius_x - right.radius_x
        if max_distance >= min_distance:
            center_distance = min(max(config.distance, min_distance), max_distance)
        else:
            center_distance = max(0.0, max_distance)
        center_x = width / 2
        left_cx = center_x - center_distance / 2
        right_cx = center_x + center_distance / 2
        eyes = (
            LayoutEye(name="left", cx=left_cx, cy=cy, style=left),
            LayoutEye(name="right", cx=right_cx, cy=cy, style=right),
        )

    return EyeLayout(width=width, height=height, eyes=eyes, max_pupil_travel=max(eye.style.pupil_travel for eye in eyes))


def hyprctl(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["hyprctl", *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=HYPRCTL_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None
    return result.stdout.strip()


def cursor_position() -> tuple[int, int] | None:
    output = hyprctl("cursorpos")
    if not output:
        return None
    match = re.search(r"(-?\d+)\s*,\s*(-?\d+)", output)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)))


def active_workspace_signature() -> str | None:
    output = hyprctl("activeworkspace", "-j")
    if not output:
        return None
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    workspace_id = data.get("id")
    workspace_name = data.get("name")
    return f"{workspace_id}:{workspace_name}"


def monitors() -> list[Monitor]:
    try:
        if MONITOR_CACHE_PATH.exists() and time_since_mtime(MONITOR_CACHE_PATH) <= MONITOR_CACHE_SECONDS:
            return monitors_from_json(MONITOR_CACHE_PATH.read_text(encoding="utf-8"))
    except OSError:
        pass

    output = hyprctl("monitors", "-j")
    if not output:
        return []
    try:
        MONITOR_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        MONITOR_CACHE_PATH.write_text(output, encoding="utf-8")
    except OSError:
        pass

    return monitors_from_json(output)


def time_since_mtime(path: Path) -> float:
    return max(0.0, time.time() - path.stat().st_mtime)


def monitors_from_json(output: str) -> list[Monitor]:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []

    found: list[Monitor] = []
    for item in data:
        try:
            found.append(
                Monitor(
                    x=int(item["x"]),
                    y=int(item["y"]),
                    width=int(item["width"]),
                    height=int(item["height"]),
                    scale=float(item.get("scale", 1.0)),
                    transform=int(item.get("transform", 0)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return found


def strip_jsonc(text: str) -> str:
    result: list[str] = []
    in_string = False
    escaped = False
    i = 0
    while i < len(text):
        char = text[i]
        next_char = text[i + 1] if i + 1 < len(text) else ""
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
            result.append(char)
            i += 1
            continue
        if char == "/" and next_char == "/":
            i += 2
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        if char == "/" and next_char == "*":
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        result.append(char)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(result))


def waybar_position() -> str:
    override = os.getenv("WAYBAR_GOOGLE_EYES_POSITION")
    if override in {"top", "bottom"}:
        return override
    try:
        state = json.loads(WAYBAR_DISPLAY_STATE_PATH.read_text(encoding="utf-8"))
        state_position = state.get("position") if isinstance(state, dict) else None
        if state_position in {"top", "bottom"}:
            return state_position
    except (OSError, json.JSONDecodeError):
        pass
    try:
        raw: Any = json.loads(strip_jsonc(WAYBAR_CONFIG_PATH.read_text(encoding="utf-8")))
        config = raw[0] if isinstance(raw, list) and raw else raw
        position = config.get("position", "bottom") if isinstance(config, dict) else "bottom"
    except (OSError, json.JSONDecodeError):
        position = "bottom"
    return "top" if position == "top" else "bottom"


def monitor_for_cursor(cursor: tuple[int, int], known: list[Monitor]) -> Monitor:
    px, py = cursor
    for monitor in known:
        if monitor.contains(px, py):
            return monitor
    if known:
        return min(
            known,
            key=lambda mon: math.hypot(px - (mon.x + mon.logical_width / 2), py - (mon.y + mon.logical_height / 2)),
        )
    return Monitor(0, 0, 1920, 1080)


def pupil_offsets_by_eye(
    cursor: tuple[int, int] | None,
    known_monitors: list[Monitor],
    position: str,
    layout: EyeLayout,
) -> dict[str, tuple[float, float]]:
    """Track from each eye's real screen-space center instead of one shared bar anchor."""
    if cursor is None or layout.max_pupil_travel <= 0:
        return {}

    px, py = cursor
    anchor_x, anchor_y = monitor_for_cursor(cursor, known_monitors).anchor(position)
    layout_center_x = layout.width / 2
    layout_center_y = layout.height / 2
    offsets: dict[str, tuple[float, float]] = {}

    for eye in layout.eyes:
        eye_x = anchor_x + (eye.cx - layout_center_x)
        eye_y = anchor_y + (eye.cy - layout_center_y)
        dx = px - eye_x
        dy = py - eye_y
        distance = math.hypot(dx, dy)
        if distance < 1.0:
            offsets[eye.name] = (0.0, 0.0)
            continue
        strength = min(1.0, distance / 260.0)
        travel = eye.style.pupil_travel * strength
        offsets[eye.name] = (dx / distance * travel, dy / distance * travel)

    return offsets


def default_blink_state() -> dict[str, Any]:
    return {
        "left": {"held": False, "until": 0.0, "held_until": 0.0},
        "right": {"held": False, "until": 0.0, "held_until": 0.0},
    }


def read_blink_state() -> dict[str, Any]:
    try:
        data = json.loads(BLINK_PATH.read_text(encoding="utf-8"))
    except (OSError, TypeError, json.JSONDecodeError):
        return default_blink_state()

    state = default_blink_state()
    for eye in ("left", "right"):
        item = data.get(eye, {}) if isinstance(data, dict) else {}
        if not isinstance(item, dict):
            continue
        state[eye]["held"] = bool(item.get("held", False))
        try:
            state[eye]["until"] = float(item.get("until", 0))
        except (TypeError, ValueError):
            state[eye]["until"] = 0.0
        try:
            state[eye]["held_until"] = float(item.get("held_until", 0))
        except (TypeError, ValueError):
            state[eye]["held_until"] = 0.0
    return state


def write_blink_state(state: dict[str, Any]) -> None:
    BLINK_PATH.parent.mkdir(parents=True, exist_ok=True)
    BLINK_PATH.write_text(json.dumps(state), encoding="utf-8")


def clear_held_blinks() -> bool:
    state = read_blink_state()
    changed = False
    for eye in ("left", "right"):
        if state[eye]["held"] or state[eye]["held_until"] > 0:
            state[eye]["held"] = False
            state[eye]["held_until"] = 0.0
            state[eye]["until"] = 0.0
            changed = True
    if changed:
        write_blink_state(state)
    return changed


def active_blinks(config: GooglyConfig) -> set[str]:
    if not config.blink.enabled:
        return set()
    now = time.monotonic()
    state = read_blink_state()
    active: set[str] = set()
    for eye in ("left", "right"):
        held_active = state[eye]["held"] and now <= state[eye]["held_until"]
        if held_active or now <= state[eye]["until"]:
            active.add(eye)
    return active


def request_blink(eye: str) -> int:
    config = load_config()
    if not config.blink.enabled:
        return 0
    if eye not in {"left", "right"}:
        print("Expected blink target: left or right", file=sys.stderr)
        return 2

    state = read_blink_state()
    state[eye]["until"] = time.monotonic() + config.blink.duration_seconds
    write_blink_state(state)
    return 0


def set_button_state(eye: str, action: str) -> int:
    config = load_config()
    if not config.blink.enabled:
        return 0
    if eye not in {"left", "right"} or action not in {"down", "up"}:
        print("Expected button state: left|right down|up", file=sys.stderr)
        return 2

    state = read_blink_state()
    state[eye]["held"] = action == "down"
    now = time.monotonic()
    state[eye]["until"] = now + config.blink.duration_seconds
    state[eye]["held_until"] = now + MAX_HELD_BLINK_SECONDS if action == "down" else 0.0
    write_blink_state(state)
    return 0


def pupil_markup(cx: float, cy: float, blink: bool, eye: EyeStyle, blink_character: str) -> str:
    color = eye.pupil_color
    if blink:
        char = html.escape(blink_character, quote=False)
        font_size = eye.blink_size
        return f'<text x="{cx:.2f}" y="{cy:.2f}" text-anchor="middle" dominant-baseline="central" font-family="serif" font-size="{font_size:.2f}" font-weight="bold" fill="{color}">{char}</text>'
    return f'''<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{eye.pupil_size}" fill="{color}"/>
  <circle cx="{cx - 1.0:.2f}" cy="{cy - 1.0:.2f}" r="0.8" fill="#ffffff" opacity="0.9"/>'''


def svg(offsets: dict[str, tuple[float, float]], config: GooglyConfig, layout: EyeLayout, blinks: set[str] | None = None) -> str:
    blinks = blinks or set()
    body: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{layout.width}" height="{layout.height}" viewBox="0 0 {layout.width} {layout.height}">'
    ]
    for eye in layout.eyes:
        style = eye.style
        body.append(
            f'  <ellipse cx="{eye.cx:.2f}" cy="{eye.cy:.2f}" rx="{style.radius_x:.2f}" ry="{style.radius_y:.2f}" fill="{style.sclera_color}" stroke="{style.outline_color}" stroke-width="1.7"/>'
        )
    for eye in layout.eyes:
        offset_x, offset_y = offsets.get(eye.name, (0.0, 0.0))
        px = eye.cx + offset_x
        py = eye.cy + offset_y
        body.append(f'  {pupil_markup(px, py, eye.name in blinks, eye.style, config.blink.character)}')
    body.append("</svg>")
    return "\n".join(body) + "\n"


def write_svg(contents: str) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=OUTPUT_PATH.parent, delete=False) as handle:
        handle.write(contents)
        temp_name = handle.name
    os.replace(temp_name, OUTPUT_PATH)


def render_frame(known_monitors: list[Monitor] | None = None, position: str | None = None) -> str:
    config = load_config()
    layout = eye_layout(config)
    position = position or waybar_position()
    cursor = cursor_position()
    offsets = pupil_offsets_by_eye(cursor, known_monitors if known_monitors is not None else monitors(), position, layout)
    return svg(offsets, config, layout, active_blinks(config))


def generate_once(known_monitors: list[Monitor] | None = None, position: str | None = None) -> str:
    contents = render_frame(known_monitors, position)
    write_svg(contents)
    return contents


def process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_matches_daemon(pid: int) -> bool:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    command = raw.replace(b"\0", b" ").decode("utf-8", errors="replace")
    return "waybar-googly-eyes.py" in command and "--daemon" in command


def claim_pid() -> bool:
    PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = int(PID_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        existing = 0
    if existing and process_running(existing) and process_matches_daemon(existing):
        return False
    PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    return True


def daemon() -> int:
    if not claim_pid():
        return 0

    known_monitors = monitors()
    position = waybar_position()
    next_monitor_refresh = time.monotonic() + MONITOR_CACHE_SECONDS
    next_position_refresh = time.monotonic() + MONITOR_CACHE_SECONDS
    last_workspace_signature = active_workspace_signature()
    next_workspace_check = time.monotonic() + WORKSPACE_POLL_SECONDS
    last_contents = ""

    try:
        while True:
            now = time.monotonic()
            if now >= next_monitor_refresh:
                known_monitors = monitors()
                next_monitor_refresh = now + MONITOR_CACHE_SECONDS
            if now >= next_position_refresh:
                position = waybar_position()
                next_position_refresh = now + MONITOR_CACHE_SECONDS
            if now >= next_workspace_check:
                workspace_signature = active_workspace_signature()
                if (
                    workspace_signature is not None
                    and last_workspace_signature is not None
                    and workspace_signature != last_workspace_signature
                    and clear_held_blinks()
                ):
                    last_contents = ""
                if workspace_signature is not None:
                    last_workspace_signature = workspace_signature
                next_workspace_check = now + WORKSPACE_POLL_SECONDS

            contents = render_frame(known_monitors, position)
            if contents != last_contents:
                write_svg(contents)
                last_contents = contents
            time.sleep(DAEMON_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        return 130
    finally:
        try:
            if PID_PATH.read_text(encoding="utf-8").strip() == str(os.getpid()):
                PID_PATH.unlink()
        except OSError:
            pass


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--daemon":
        return daemon()
    if len(sys.argv) > 2 and sys.argv[1] == "--blink":
        return request_blink(sys.argv[2])
    if len(sys.argv) > 3 and sys.argv[1] == "--button":
        return set_button_state(sys.argv[2], sys.argv[3])

    position = waybar_position()
    generate_once(position=position)
    print(OUTPUT_PATH)
    print("Googly eyes follow the cursor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
