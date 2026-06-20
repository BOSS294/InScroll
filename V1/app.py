import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

from flask import Flask, jsonify, render_template, request

try:
    import pyautogui
    pyautogui.FAILSAFE = False
except Exception:  # pragma: no cover
    pyautogui = None

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "mode": "preview",  # preview | skeleton
    "hands": {
        "maxNumHands": 2,
        "modelComplexity": 1,
        "minDetectionConfidence": 0.75,
        "minTrackingConfidence": 0.65,
    },
    "cooldownMs": 700,
    "bindings": {
        "thumb_up": {"enabled": True, "type": "key", "value": "space"},
        "thumb_down": {"enabled": False, "type": "key", "value": "backspace"},
        "fist": {"enabled": True, "type": "key", "value": "f"},
        "open_palm": {"enabled": False, "type": "key", "value": "p"},
        "swipe_up": {"enabled": True, "type": "key", "value": "up"},
        "swipe_down": {"enabled": True, "type": "key", "value": "down"},
        "swipe_left": {"enabled": False, "type": "key", "value": "left"},
        "swipe_right": {"enabled": False, "type": "key", "value": "right"},
        "pinch": {"enabled": False, "type": "key", "value": "enter"},
    },
}

ACTION_LOCK = threading.Lock()
LAST_TRIGGER_AT: Dict[str, float] = {}
EVENT_LOG: List[Dict[str, Any]] = []
MAX_EVENT_LOG = 80

app = Flask(__name__, template_folder="templates", static_folder="static")


def deep_merge(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
            return deep_merge(DEFAULT_CONFIG, loaded)
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(cfg: Dict[str, Any]) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def normalize_key_sequence(value: str) -> List[str]:
    parts = [p.strip().lower() for p in value.replace("+", ",").split(",") if p.strip()]
    aliases = {
        "ctrl": "ctrl",
        "control": "ctrl",
        "alt": "alt",
        "shift": "shift",
        "cmd": "command",
        "command": "command",
        "win": "win",
        "windows": "win",
        "esc": "esc",
        "escape": "esc",
        "spacebar": "space",
        "space": "space",
        "enter": "enter",
        "return": "enter",
        "up": "up",
        "down": "down",
        "left": "left",
        "right": "right",
        "backspace": "backspace",
        "delete": "delete",
        "tab": "tab",
        "pageup": "pageup",
        "pagedown": "pagedown",
    }
    return [aliases.get(p, p) for p in parts]


def execute_binding(binding: Dict[str, Any]) -> str:
    if pyautogui is None:
        raise RuntimeError("pyautogui is not installed or not available on this system")

    action_type = binding.get("type", "key")
    raw_value = str(binding.get("value", "")).strip()

    if action_type == "key":
        if not raw_value:
            raise ValueError("Key action requires a key name")
        pyautogui.press(raw_value.lower())
        return f"pressed {raw_value}"

    if action_type == "hotkey":
        keys = normalize_key_sequence(raw_value)
        if len(keys) < 2:
            raise ValueError("Hotkey requires at least two keys, e.g. ctrl,shift,z")
        pyautogui.hotkey(*keys)
        return f"hotkey {keys}"

    if action_type == "scroll":
        amount = int(raw_value or "0")
        if amount == 0:
            raise ValueError("Scroll requires a non-zero amount")
        pyautogui.scroll(amount)
        return f"scrolled {amount}"

    raise ValueError(f"Unsupported action type: {action_type}")


def can_fire(gesture: str, cooldown_ms: int) -> bool:
    now = time.time() * 1000.0
    with ACTION_LOCK:
        last = LAST_TRIGGER_AT.get(gesture, 0.0)
        if now - last < cooldown_ms:
            return False
        LAST_TRIGGER_AT[gesture] = now
        return True


def log_event(entry: Dict[str, Any]) -> None:
    EVENT_LOG.insert(0, entry)
    del EVENT_LOG[MAX_EVENT_LOG:]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        return jsonify(load_config())

    incoming = request.get_json(force=True, silent=True) or {}
    cfg = load_config()

    if isinstance(incoming.get("mode"), str):
        cfg["mode"] = incoming["mode"]

    if isinstance(incoming.get("hands"), dict):
        cfg["hands"] = deep_merge(cfg.get("hands", {}), incoming["hands"])

    if isinstance(incoming.get("cooldownMs"), (int, float)):
        cfg["cooldownMs"] = max(0, int(incoming["cooldownMs"]))

    if isinstance(incoming.get("bindings"), dict):
        cfg["bindings"] = deep_merge(cfg.get("bindings", {}), incoming["bindings"])

    save_config(cfg)
    return jsonify({"ok": True, "config": cfg})


@app.route("/api/trigger", methods=["POST"])
def api_trigger():
    payload = request.get_json(force=True, silent=True) or {}
    gesture = str(payload.get("gesture", "")).strip().lower()
    hand = str(payload.get("hand", "")).strip()
    confidence = float(payload.get("confidence", 0.0) or 0.0)

    cfg = load_config()
    binding = cfg.get("bindings", {}).get(gesture)

    if not gesture:
        return jsonify({"ok": False, "reason": "missing_gesture"}), 400

    if not binding or not binding.get("enabled", False):
        entry = {"ts": time.time(), "gesture": gesture, "hand": hand, "status": "ignored", "reason": "not_bound", "confidence": confidence}
        log_event(entry)
        return jsonify({"ok": True, "status": "ignored", "reason": "not_bound"})

    cooldown_ms = int(cfg.get("cooldownMs", 700))
    if not can_fire(gesture, cooldown_ms):
        return jsonify({"ok": True, "status": "ignored", "reason": "cooldown"})

    try:
        action_result = execute_binding(binding)
        entry = {"ts": time.time(), "gesture": gesture, "hand": hand, "status": "triggered", "action": action_result, "confidence": confidence}
        log_event(entry)
        return jsonify({"ok": True, "status": "triggered", "action": action_result})
    except Exception as exc:
        entry = {"ts": time.time(), "gesture": gesture, "hand": hand, "status": "error", "error": str(exc), "confidence": confidence}
        log_event(entry)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/logs")
def api_logs():
    return jsonify({"events": EVENT_LOG[:30]})


@app.route("/api/reset", methods=["POST"])
def api_reset():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    save_config(cfg)
    LAST_TRIGGER_AT.clear()
    EVENT_LOG.clear()
    return jsonify({"ok": True, "config": cfg})


if __name__ == "__main__":
    if not CONFIG_PATH.exists():
        save_config(json.loads(json.dumps(DEFAULT_CONFIG)))
    app.run(host="127.0.0.1", port=7860, debug=True)
