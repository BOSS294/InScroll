import json
import math
import platform
import threading
import time
from collections import deque
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple
import sys
import ctypes
import os

# ============================================================================
# AUTO-PATCH FOR PYTHON 3.14+ MEDIAPIPE COMPATIBILITY
# ============================================================================
def _auto_patch_mediapipe():
    """Automatically patch MediaPipe for Python 3.14+ compatibility"""
    if sys.version_info < (3, 14):
        return  # Not needed
    
    try:
        import mediapipe
        from pathlib import Path
        
        mediapipe_path = Path(mediapipe.__file__).parent
        bindings_file = mediapipe_path / "tasks" / "python" / "core" / "mediapipe_c_bindings.py"
        
        if not bindings_file.exists():
            return
        
        content = bindings_file.read_text(encoding='utf-8')
        
        # Check if already patched
        if "PYTHON314_PATCH_APPLIED" in content:
            return
        
        # Apply the patch
        original = '_shared_lib.free.argtypes = [ctypes.c_void_p]'
        if original in content:
            patched = '''# PYTHON314_PATCH_APPLIED
  try:
    _shared_lib.free.argtypes = [ctypes.c_void_p]
    _shared_lib.free.restype = None
  except AttributeError:
    pass  # Python 3.14+ compatibility: 'free' may not be available'''
            
            # Find and replace the full function
            lines = content.split('\n')
            new_lines = []
            i = 0
            while i < len(lines):
                if '_shared_lib.free.argtypes = [ctypes.c_void_p]' in lines[i]:
                    # Replace this line and the next
                    new_lines.append('  try:')
                    new_lines.append('    _shared_lib.free.argtypes = [ctypes.c_void_p]')
                    if i + 1 < len(lines) and '_shared_lib.free.restype' in lines[i + 1]:
                        new_lines.append('    _shared_lib.free.restype = None')
                        i += 2
                    else:
                        i += 1
                    new_lines.append('  except AttributeError:')
                    new_lines.append('    pass  # PYTHON314_PATCH_APPLIED: Python 3.14+ compatibility')
                else:
                    new_lines.append(lines[i])
                    i += 1
            
            new_content = '\n'.join(new_lines)
            bindings_file.write_text(new_content, encoding='utf-8')
    except Exception as e:
        print(f"Auto-patch warning (non-fatal): {e}", file=sys.stderr)

# Apply patch on import
_auto_patch_mediapipe()

# ============================================================================
# IMPORTS
# ============================================================================
import cv2
import numpy as np
from PIL import Image, ImageTk
import urllib

try:
    import pyautogui
    pyautogui.FAILSAFE = False
except Exception:
    pyautogui = None

import tkinter as tk
from tkinter import messagebox, ttk

# MediaPipe import
try:
    import mediapipe as mp
    MEDIAPIPE_AVAILABLE = True
except ImportError as e:
    mp = None
    MEDIAPIPE_AVAILABLE = False
    print(f"Warning: MediaPipe import issue: {e}")

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
MODEL_PATH = BASE_DIR / "models" / "hand_landmarker.task"
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"


DEFAULT_CONFIG: Dict[str, Any] = {
    "camera_index": 0,
    "camera_width": 960,
    "camera_height": 540,
    "preview_flip": True,
    "detection": {
        "max_num_hands": 2,
        "model_complexity": 1,  # kept for compatibility with older config UI
        "min_detection_confidence": 0.82,
        "min_tracking_confidence": 0.78,
    },
    "gesture": {
        "stable_frames": 3,
        "cooldown_ms": 450,
        "swipe_min_dx": 0.13,
        "swipe_min_dy": 0.12,
        "swipe_min_duration_ms": 80,
        "swipe_max_duration_ms": 280,
        "pinch_threshold": 0.042,
        "open_palm_min_extended": 4,
    },
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
    "calibration": {
        "neutral_center": {"x": 0.5, "y": 0.5},
        "swipe_scale": {"x": 1.0, "y": 1.0},
        "recent_center_samples": [],
        "recent_swipe_samples": [],
    },
}


# MediaPipe Hands connections for a green skeleton overlay.
HAND_CONNECTIONS: Tuple[Tuple[int, int], ...] = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
)


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
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return deep_merge(DEFAULT_CONFIG, data)
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(cfg: Dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


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
        raise RuntimeError("pyautogui is not installed")

    action_type = str(binding.get("type", "key")).lower()
    raw_value = str(binding.get("value", "")).strip()

    if action_type == "key":
        if not raw_value:
            raise ValueError("Key action needs a key value")
        pyautogui.press(raw_value.lower())
        return f"pressed {raw_value}"

    if action_type == "hotkey":
        keys = normalize_key_sequence(raw_value)
        if len(keys) < 2:
            raise ValueError("Hotkey needs at least two keys")
        pyautogui.hotkey(*keys)
        return f"hotkey {keys}"

    if action_type == "scroll":
        amount = int(raw_value or "0")
        if amount == 0:
            raise ValueError("Scroll needs a non-zero amount")
        pyautogui.scroll(amount)
        return f"scrolled {amount}"

    raise ValueError(f"Unsupported action type: {action_type}")


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def point_distance(a: Dict[str, float], b: Dict[str, float]) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def landmarks_center(landmarks) -> Dict[str, float]:
    x = sum(p.x for p in landmarks) / len(landmarks)
    y = sum(p.y for p in landmarks) / len(landmarks)
    return {"x": float(x), "y": float(y)}


def finger_extended(landmarks, tip: int, pip: int) -> bool:
    return landmarks[tip].y < landmarks[pip].y


def thumb_extended(landmarks, handedness: str) -> bool:
    tip = landmarks[4]
    ip = landmarks[3]
    wrist = landmarks[0]
    index_mcp = landmarks[5]

    # If handedness is unstable, the secondary vertical checks still help.
    if handedness.lower() == "right":
        horizontal = tip.x < ip.x - 0.012
    else:
        horizontal = tip.x > ip.x + 0.012

    spread = abs(tip.y - wrist.y) < abs(index_mcp.y - wrist.y) + 0.18
    return horizontal and spread


def classify_gesture(landmarks, handedness: str, cfg: Dict[str, Any]) -> Optional[str]:
    gesture_cfg = cfg["gesture"]
    pinch_threshold = float(gesture_cfg["pinch_threshold"])

    fingers = {
        "thumb": thumb_extended(landmarks, handedness),
        "index": finger_extended(landmarks, 8, 6),
        "middle": finger_extended(landmarks, 12, 10),
        "ring": finger_extended(landmarks, 16, 14),
        "pinky": finger_extended(landmarks, 20, 18),
    }

    extended_count = sum(1 for v in fingers.values() if v)
    pinch_distance = point_distance(
        {"x": landmarks[4].x, "y": landmarks[4].y},
        {"x": landmarks[8].x, "y": landmarks[8].y},
    )

    wrist = landmarks[0]
    thumb_tip = landmarks[4]
    thumb_ip = landmarks[3]
    thumb_mcp = landmarks[2]

    if pinch_distance <= pinch_threshold and extended_count <= 2:
        return "pinch"

    if extended_count <= 1:
        return "fist"

    if fingers["thumb"] and not fingers["index"] and not fingers["middle"] and not fingers["ring"] and not fingers["pinky"]:
        upward = thumb_tip.y < thumb_ip.y and thumb_tip.y < thumb_mcp.y - 0.004
        downward = thumb_tip.y > thumb_ip.y and thumb_tip.y > thumb_mcp.y + 0.004
        if upward:
            return "thumb_up"
        if downward:
            return "thumb_down"

    if extended_count >= int(gesture_cfg["open_palm_min_extended"]):
        tip_lift = (
            (wrist.y - landmarks[8].y)
            + (wrist.y - landmarks[12].y)
            + (wrist.y - landmarks[16].y)
            + (wrist.y - landmarks[20].y)
        ) / 4.0
        if tip_lift > 0.10:
            return "open_palm"

    return None


def draw_text_box(frame, text: str, x: int, y: int) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.6
    thickness = 1
    (tw, th), baseline = cv2.getTextSize(text, font, scale, thickness)
    pad_x, pad_y = 10, 8
    x = max(8, min(x, frame.shape[1] - tw - pad_x * 2 - 8))
    y = max(30, min(y, frame.shape[0] - 8))
    cv2.rectangle(frame, (x, y - th - pad_y * 2), (x + tw + pad_x * 2, y + baseline + pad_y), (12, 20, 16), -1)
    cv2.rectangle(frame, (x, y - th - pad_y * 2), (x + tw + pad_x * 2, y + baseline + pad_y), (40, 140, 70), 1)
    cv2.putText(frame, text, (x + pad_x, y - pad_y), font, scale, (230, 255, 235), thickness, cv2.LINE_AA)


def _landmark_to_px(landmark, frame) -> Tuple[int, int]:
    return int(landmark.x * frame.shape[1]), int(landmark.y * frame.shape[0])


def draw_hand_skeleton(frame: np.ndarray, landmarks) -> None:
    points = [_landmark_to_px(p, frame) for p in landmarks]
    # connections
    for a, b in HAND_CONNECTIONS:
        cv2.line(frame, points[a], points[b], (34, 197, 94), 3, cv2.LINE_AA)
    # joints
    for idx, pt in enumerate(points):
        radius = 5 if idx == 0 else 4
        cv2.circle(frame, pt, radius, (134, 239, 172), -1, cv2.LINE_AA)
        cv2.circle(frame, pt, max(1, radius - 2), (16, 64, 35), -1, cv2.LINE_AA)


def get_category_label(cat: Any) -> str:
    for attr in ("category_name", "display_name", "name"):
        value = getattr(cat, attr, None)
        if value:
            return str(value)
    return str(cat)


def ensure_model_downloaded() -> Path:
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if MODEL_PATH.exists() and MODEL_PATH.stat().st_size > 0:
        return MODEL_PATH

    tmp_path = MODEL_PATH.with_suffix(".task.download")
    try:
        urllib.request.urlretrieve(MODEL_URL, tmp_path)
        tmp_path.replace(MODEL_PATH)
    except Exception as exc:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        raise RuntimeError(
            "Could not download the MediaPipe hand model. "
            "Check your internet connection or place hand_landmarker.task in the models folder."
        ) from exc
    return MODEL_PATH


@dataclass
class HandSample:
    t: float
    x: float
    y: float


class ScrollableFrame(ttk.Frame):
    def __init__(self, master, *args, **kwargs):
        super().__init__(master, *args, **kwargs)
        canvas = tk.Canvas(self, highlightthickness=0, bg="#0b1220")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.inner = ttk.Frame(canvas)
        self.inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        window = canvas.create_window((0, 0), window=self.inner, anchor="nw")

        def _on_canvas_configure(event):
            canvas.itemconfigure(window, width=event.width)

        canvas.bind("<Configure>", _on_canvas_configure)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.canvas = canvas


class GestureApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("InScroll — Desktop Gesture Control")
        self.geometry("1450x920")
        self.minsize(1280, 820)
        self.configure(bg="#08111e")

        self.cfg = load_config()
        self.preview_running = False
        self.stop_event = threading.Event()
        self.capture_thread: Optional[threading.Thread] = None
        self.video_capture: Optional[cv2.VideoCapture] = None
        self.hand_landmarker = None

        self.latest_frame_lock = threading.Lock()
        self.latest_pil_image: Optional[Image.Image] = None
        self.latest_status: Dict[str, Any] = {}
        self.current_fps = 0.0
        self.events: List[Dict[str, Any]] = []
        self.hand_histories: Dict[str, Deque[HandSample]] = {}
        self.stable_gesture: Dict[str, Optional[str]] = {}
        self.stable_count: Dict[str, int] = {}
        self.last_trigger_at: Dict[str, float] = {}
        self.frame_ts_ms = 0

        self.calibration_state = {
            "capture_center": False,
            "capture_swipe": False,
            "swipe_samples": [],
            "center_samples": [],
            "countdown": 0.0,
        }

        self._build_styles()
        self._build_layout()
        self._load_ui_from_config()
        self._schedule_ui_updates()

    def _build_styles(self):
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure("TFrame", background="#08111e")
        style.configure("Card.TFrame", background="#0d1726", relief="flat")
        style.configure("TLabel", background="#08111e", foreground="#e5eefc")
        style.configure("Muted.TLabel", background="#08111e", foreground="#8da2bf")
        style.configure("Card.TLabel", background="#0d1726", foreground="#e5eefc")
        style.configure("Title.TLabel", background="#08111e", foreground="#f5f8ff", font=("Segoe UI", 24, "bold"))
        style.configure("Subtitle.TLabel", background="#08111e", foreground="#8da2bf", font=("Segoe UI", 10))
        style.configure("Tab.TNotebook", background="#08111e", borderwidth=0)
        style.configure("Tab.TNotebook.Tab", padding=(18, 10), background="#0d1726", foreground="#d8e6f8")
        style.map("Tab.TNotebook.Tab", background=[("selected", "#1a2740")], foreground=[("selected", "#ffffff")])
        style.configure("TButton", padding=(12, 9), background="#1a2740", foreground="#ffffff", borderwidth=0)
        style.map("TButton", background=[("active", "#23365a")])
        style.configure("Accent.TButton", padding=(12, 10), background="#19c37d", foreground="#07111f", font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#30d48c")])
        style.configure("Danger.TButton", padding=(12, 10), background="#4b2230", foreground="#ffdce6")
        style.map("Danger.TButton", background=[("active", "#652c40")])
        style.configure("TLabelframe", background="#0d1726", foreground="#e5eefc")
        style.configure("TLabelframe.Label", background="#0d1726", foreground="#a9b9d1")
        style.configure("TCheckbutton", background="#0d1726", foreground="#e5eefc")
        style.configure("TRadiobutton", background="#08111e", foreground="#e5eefc")
        style.configure("TEntry", fieldbackground="#111c2e", foreground="#e5eefc", insertcolor="#ffffff")
        style.configure("TCombobox", fieldbackground="#111c2e", foreground="#e5eefc")
        style.configure("Horizontal.TScale", background="#0d1726")

    def _build_layout(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=18, pady=(16, 10))

        title_wrap = ttk.Frame(top)
        title_wrap.pack(side="left", fill="x", expand=True)
        ttk.Label(title_wrap, text="InScroll", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            title_wrap,
            text="Desktop hand gesture control with MediaPipe Tasks, live preview, configuration, and calibration.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        self.status_pill = tk.Label(
            top,
            text="Camera idle",
            bg="#101a2c",
            fg="#d9e8ff",
            padx=14,
            pady=8,
            font=("Segoe UI", 10, "bold"),
            bd=0,
            relief="flat",
        )
        self.status_pill.pack(side="right")

        self.notebook = ttk.Notebook(self, style="Tab.TNotebook")
        self.notebook.pack(fill="both", expand=True, padx=18, pady=(0, 18))

        self.preview_tab = ttk.Frame(self.notebook)
        self.config_tab = ttk.Frame(self.notebook)
        self.calib_tab = ttk.Frame(self.notebook)

        self.notebook.add(self.preview_tab, text="Preview")
        self.notebook.add(self.config_tab, text="Configurations")
        self.notebook.add(self.calib_tab, text="Calibration")

        self._build_preview_tab()
        self._build_config_tab()
        self._build_calibration_tab()

    def _build_preview_tab(self):
        outer = ttk.Frame(self.preview_tab)
        outer.pack(fill="both", expand=True, padx=2, pady=2)
        outer.columnconfigure(0, weight=3)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        left = ttk.Frame(outer, style="Card.TFrame", padding=14)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right = ttk.Frame(outer, style="Card.TFrame", padding=14)
        right.grid(row=0, column=1, sticky="nsew")

        stage_wrap = ttk.Frame(left, style="Card.TFrame")
        stage_wrap.pack(fill="both", expand=True)
        stage_header = ttk.Frame(stage_wrap, style="Card.TFrame")
        stage_header.pack(fill="x", pady=(0, 10))
        ttk.Label(stage_header, text="Live Preview", style="Card.TLabel", font=("Segoe UI", 15, "bold")).pack(side="left")
        ttk.Label(stage_header, text="green skeleton overlay + mirrored view", style="Muted.TLabel").pack(side="right")

        self.preview_canvas = tk.Canvas(stage_wrap, bg="#02070c", highlightthickness=1, highlightbackground="#1f2b44")
        self.preview_canvas.pack(fill="both", expand=True)

        action_bar = ttk.Frame(left, style="Card.TFrame")
        action_bar.pack(fill="x", pady=(12, 0))
        self.start_btn = ttk.Button(action_bar, text="Start Camera", style="Accent.TButton", command=self.toggle_camera)
        self.start_btn.pack(side="left")
        ttk.Button(action_bar, text="Stop", command=self.stop_camera).pack(side="left", padx=(10, 0))
        ttk.Button(action_bar, text="Save Config", command=self.save_config).pack(side="left", padx=(10, 0))
        ttk.Button(action_bar, text="Reload Config", command=self.reload_config).pack(side="left", padx=(10, 0))

        stat_card = ttk.LabelFrame(right, text="Realtime Stats")
        stat_card.pack(fill="x")
        self.lbl_hand_count = ttk.Label(stat_card, text="Hands: 0", style="Card.TLabel", font=("Segoe UI", 11, "bold"))
        self.lbl_hand_count.pack(anchor="w", pady=(6, 2), padx=10)
        self.lbl_last_gesture = ttk.Label(stat_card, text="Last gesture: —", style="Card.TLabel")
        self.lbl_last_gesture.pack(anchor="w", pady=2, padx=10)
        self.lbl_track_status = ttk.Label(stat_card, text="Status: idle", style="Card.TLabel")
        self.lbl_track_status.pack(anchor="w", pady=2, padx=10)
        self.lbl_fps = ttk.Label(stat_card, text="FPS: 0", style="Card.TLabel")
        self.lbl_fps.pack(anchor="w", pady=(2, 10), padx=10)

        guide_card = ttk.LabelFrame(right, text="Use Notes")
        guide_card.pack(fill="both", expand=True, pady=(12, 0))
        notes = [
            "Face the laptop camera directly.",
            "Keep your palm inside the center guide during calibration.",
            "Swipe fast and clean for swipe gestures.",
            "The preview stays mirrored so it feels natural on a laptop camera.",
        ]
        for note in notes:
            ttk.Label(guide_card, text="• " + note, style="Card.TLabel", wraplength=300, justify="left").pack(anchor="w", padx=10, pady=4)

        self.events_card = ttk.LabelFrame(right, text="Event Log")
        self.events_card.pack(fill="both", expand=True, pady=(12, 0))
        self.events_text = tk.Text(
            self.events_card,
            height=10,
            bg="#0a1322",
            fg="#dce9fb",
            insertbackground="#ffffff",
            bd=0,
            highlightthickness=0,
            wrap="word",
        )
        self.events_text.pack(fill="both", expand=True, padx=8, pady=8)
        self.events_text.configure(state="disabled")

    def _build_config_tab(self):
        outer = ttk.Frame(self.config_tab)
        outer.pack(fill="both", expand=True, padx=2, pady=2)
        outer.columnconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        left = ttk.Frame(outer, style="Card.TFrame", padding=16)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right = ttk.Frame(outer, style="Card.TFrame", padding=16)
        right.grid(row=0, column=1, sticky="nsew")

        camera_box = ttk.LabelFrame(left, text="Camera + Detection")
        camera_box.pack(fill="x")
        self.var_cam_index = tk.StringVar()
        self.var_cam_width = tk.StringVar()
        self.var_cam_height = tk.StringVar()
        self.var_preview_flip = tk.BooleanVar()
        self.var_max_hands = tk.StringVar()
        self.var_model_complexity = tk.StringVar()
        self.var_min_det = tk.DoubleVar()
        self.var_min_track = tk.DoubleVar()

        self._add_labeled_entry(camera_box, "Camera index", self.var_cam_index)
        self._add_labeled_entry(camera_box, "Frame width", self.var_cam_width)
        self._add_labeled_entry(camera_box, "Frame height", self.var_cam_height)

        flip_row = ttk.Frame(camera_box)
        flip_row.pack(fill="x", padx=10, pady=6)
        ttk.Checkbutton(flip_row, text="Mirror preview", variable=self.var_preview_flip).pack(anchor="w")

        self._add_labeled_entry(camera_box, "Max hands", self.var_max_hands)
        self._add_labeled_entry(camera_box, "Model complexity", self.var_model_complexity)
        self._add_labeled_scale(camera_box, "Detection confidence", self.var_min_det, 0.50, 0.95)
        self._add_labeled_scale(camera_box, "Tracking confidence", self.var_min_track, 0.50, 0.95)

        gesture_box = ttk.LabelFrame(left, text="Gesture Engine")
        gesture_box.pack(fill="x", pady=(12, 0))
        self.var_stable_frames = tk.StringVar()
        self.var_cooldown_ms = tk.StringVar()
        self.var_swipe_dx = tk.DoubleVar()
        self.var_swipe_dy = tk.DoubleVar()
        self.var_swipe_min_ms = tk.StringVar()
        self.var_swipe_max_ms = tk.StringVar()
        self.var_pinch_threshold = tk.DoubleVar()
        self.var_open_palm_min = tk.StringVar()

        self._add_labeled_entry(gesture_box, "Stable frames", self.var_stable_frames)
        self._add_labeled_entry(gesture_box, "Gesture cooldown ms", self.var_cooldown_ms)
        self._add_labeled_scale(gesture_box, "Swipe min dx", self.var_swipe_dx, 0.05, 0.30)
        self._add_labeled_scale(gesture_box, "Swipe min dy", self.var_swipe_dy, 0.05, 0.30)
        self._add_labeled_entry(gesture_box, "Swipe min duration ms", self.var_swipe_min_ms)
        self._add_labeled_entry(gesture_box, "Swipe max duration ms", self.var_swipe_max_ms)
        self._add_labeled_scale(gesture_box, "Pinch threshold", self.var_pinch_threshold, 0.02, 0.08)
        self._add_labeled_entry(gesture_box, "Open palm min fingers", self.var_open_palm_min)

        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="Save Config", style="Accent.TButton", command=self.save_config).pack(side="left")
        ttk.Button(btns, text="Apply from File", command=self.reload_config).pack(side="left", padx=(10, 0))
        ttk.Button(btns, text="Restore Defaults", style="Danger.TButton", command=self.restore_defaults).pack(side="left", padx=(10, 0))

        bindings_box = ttk.LabelFrame(right, text="Gesture Bindings")
        bindings_box.pack(fill="both", expand=True)
        self.bindings_scroll = ScrollableFrame(bindings_box)
        self.bindings_scroll.pack(fill="both", expand=True, padx=6, pady=6)
        self.binding_rows: Dict[str, Dict[str, Any]] = {}
        self._build_binding_rows(self.bindings_scroll.inner)

    def _build_binding_rows(self, parent):
        gestures = [
            ("thumb_up", "Thumb Up"),
            ("thumb_down", "Thumb Down"),
            ("fist", "Fist"),
            ("open_palm", "Open Palm"),
            ("swipe_up", "Swipe Up"),
            ("swipe_down", "Swipe Down"),
            ("swipe_left", "Swipe Left"),
            ("swipe_right", "Swipe Right"),
            ("pinch", "Pinch"),
        ]
        for idx, (key, label) in enumerate(gestures):
            card = ttk.Frame(parent, style="Card.TFrame", padding=10)
            card.grid(row=idx, column=0, sticky="ew", padx=4, pady=5)
            parent.grid_columnconfigure(0, weight=1)

            var_enabled = tk.BooleanVar()
            var_type = tk.StringVar()
            var_value = tk.StringVar()

            ttk.Label(card, text=label, style="Card.TLabel", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
            ttk.Checkbutton(card, text="Enabled", variable=var_enabled).grid(row=0, column=1, sticky="e")
            ttk.Label(card, text="Action type", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 2))
            combo = ttk.Combobox(card, textvariable=var_type, values=["key", "hotkey", "scroll"], state="readonly", width=12)
            combo.grid(row=1, column=1, sticky="ew", pady=(8, 2))
            ttk.Label(card, text="Value", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 2))
            entry = ttk.Entry(card, textvariable=var_value)
            entry.grid(row=2, column=1, sticky="ew", pady=(8, 2))
            card.grid_columnconfigure(1, weight=1)
            self.binding_rows[key] = {
                "enabled": var_enabled,
                "type": var_type,
                "value": var_value,
            }

    def _add_labeled_entry(self, parent, label: str, variable: tk.Variable):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=10, pady=4)
        ttk.Label(row, text=label, style="Card.TLabel").pack(anchor="w")
        ttk.Entry(row, textvariable=variable).pack(fill="x", pady=(3, 0))

    def _add_labeled_scale(self, parent, label: str, variable: tk.DoubleVar, low: float, high: float):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=10, pady=4)
        top = ttk.Frame(row)
        top.pack(fill="x")
        ttk.Label(top, text=label, style="Card.TLabel").pack(side="left")
        ttk.Label(top, textvariable=variable, style="Muted.TLabel").pack(side="right")
        scale = ttk.Scale(row, from_=low, to=high, variable=variable)
        scale.pack(fill="x", pady=(4, 0))

    def _build_calibration_tab(self):
        outer = ttk.Frame(self.calib_tab)
        outer.pack(fill="both", expand=True, padx=2, pady=2)
        outer.columnconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)

        left = ttk.Frame(outer, style="Card.TFrame", padding=16)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right = ttk.Frame(outer, style="Card.TFrame", padding=16)
        right.grid(row=0, column=1, sticky="nsew")

        instr = ttk.LabelFrame(left, text="Calibration Workflow")
        instr.pack(fill="x")
        lines = [
            "1. Place your hand at the center of the guide box.",
            "2. Click Capture Center while holding a neutral pose.",
            "3. Then perform one fast swipe and click Calibrate Swipe.",
            "4. Save config to keep the tuned thresholds.",
        ]
        for line in lines:
            ttk.Label(instr, text="• " + line, style="Card.TLabel", wraplength=520, justify="left").pack(anchor="w", padx=10, pady=4)

        status_box = ttk.LabelFrame(left, text="Calibration State")
        status_box.pack(fill="x", pady=(12, 0))
        self.calib_status = ttk.Label(status_box, text="Ready", style="Card.TLabel")
        self.calib_status.pack(anchor="w", padx=10, pady=(8, 4))
        self.center_status = ttk.Label(status_box, text="Neutral center: not captured", style="Card.TLabel")
        self.center_status.pack(anchor="w", padx=10, pady=4)
        self.swipe_status = ttk.Label(status_box, text="Swipe scale: default", style="Card.TLabel")
        self.swipe_status.pack(anchor="w", padx=10, pady=(4, 10))

        action = ttk.Frame(left)
        action.pack(fill="x", pady=(12, 0))
        ttk.Button(action, text="Capture Center", style="Accent.TButton", command=self.capture_center_calibration).pack(side="left")
        ttk.Button(action, text="Calibrate Swipe", command=self.capture_swipe_calibration).pack(side="left", padx=(10, 0))
        ttk.Button(action, text="Reset Calibration", style="Danger.TButton", command=self.reset_calibration).pack(side="left", padx=(10, 0))

        tuning_box = ttk.LabelFrame(right, text="Calibration Values")
        tuning_box.pack(fill="x")
        self.calib_values = tk.Text(
            tuning_box,
            height=14,
            bg="#0a1322",
            fg="#dce9fb",
            insertbackground="#ffffff",
            bd=0,
            highlightthickness=0,
            wrap="word",
        )
        self.calib_values.pack(fill="x", padx=8, pady=8)

        live_box = ttk.LabelFrame(right, text="Live Calibration Feedback")
        live_box.pack(fill="both", expand=True, pady=(12, 0))
        self.live_feedback = tk.Text(
            live_box,
            height=10,
            bg="#0a1322",
            fg="#dce9fb",
            insertbackground="#ffffff",
            bd=0,
            highlightthickness=0,
            wrap="word",
        )
        self.live_feedback.pack(fill="both", expand=True, padx=8, pady=8)

    def _load_ui_from_config(self):
        self.var_cam_index.set(str(self.cfg.get("camera_index", 0)))
        self.var_cam_width.set(str(self.cfg.get("camera_width", 960)))
        self.var_cam_height.set(str(self.cfg.get("camera_height", 540)))
        self.var_preview_flip.set(bool(self.cfg.get("preview_flip", True)))

        det = self.cfg.get("detection", {})
        gest = self.cfg.get("gesture", {})
        self.var_max_hands.set(str(det.get("max_num_hands", 2)))
        self.var_model_complexity.set(str(det.get("model_complexity", 1)))
        self.var_min_det.set(float(det.get("min_detection_confidence", 0.82)))
        self.var_min_track.set(float(det.get("min_tracking_confidence", 0.78)))

        self.var_stable_frames.set(str(gest.get("stable_frames", 3)))
        self.var_cooldown_ms.set(str(gest.get("cooldown_ms", 450)))
        self.var_swipe_dx.set(float(gest.get("swipe_min_dx", 0.13)))
        self.var_swipe_dy.set(float(gest.get("swipe_min_dy", 0.12)))
        self.var_swipe_min_ms.set(str(gest.get("swipe_min_duration_ms", 80)))
        self.var_swipe_max_ms.set(str(gest.get("swipe_max_duration_ms", 280)))
        self.var_pinch_threshold.set(float(gest.get("pinch_threshold", 0.042)))
        self.var_open_palm_min.set(str(gest.get("open_palm_min_extended", 4)))

        for key, row in self.binding_rows.items():
            b = self.cfg["bindings"].get(key, {})
            row["enabled"].set(bool(b.get("enabled", False)))
            row["type"].set(str(b.get("type", "key")))
            row["value"].set(str(b.get("value", "")))

        self._refresh_calibration_texts()

    def _gather_ui_to_config(self) -> Dict[str, Any]:
        cfg = json.loads(json.dumps(self.cfg))
        cfg["camera_index"] = int(self.var_cam_index.get() or 0)
        cfg["camera_width"] = int(self.var_cam_width.get() or 960)
        cfg["camera_height"] = int(self.var_cam_height.get() or 540)
        cfg["preview_flip"] = bool(self.var_preview_flip.get())

        cfg["detection"] = {
            "max_num_hands": int(self.var_max_hands.get() or 2),
            "model_complexity": int(self.var_model_complexity.get() or 1),
            "min_detection_confidence": float(self.var_min_det.get()),
            "min_tracking_confidence": float(self.var_min_track.get()),
        }

        cfg["gesture"] = {
            "stable_frames": max(1, int(self.var_stable_frames.get() or 3)),
            "cooldown_ms": max(0, int(self.var_cooldown_ms.get() or 450)),
            "swipe_min_dx": float(self.var_swipe_dx.get()),
            "swipe_min_dy": float(self.var_swipe_dy.get()),
            "swipe_min_duration_ms": max(40, int(self.var_swipe_min_ms.get() or 80)),
            "swipe_max_duration_ms": max(100, int(self.var_swipe_max_ms.get() or 280)),
            "pinch_threshold": float(self.var_pinch_threshold.get()),
            "open_palm_min_extended": max(3, int(self.var_open_palm_min.get() or 4)),
        }

        new_bindings = {}
        for key, row in self.binding_rows.items():
            new_bindings[key] = {
                "enabled": bool(row["enabled"].get()),
                "type": row["type"].get() or "key",
                "value": row["value"].get() or "",
            }
        cfg["bindings"] = new_bindings
        return cfg

    def _refresh_calibration_texts(self):
        calib = self.cfg.get("calibration", {})
        neutral = calib.get("neutral_center", {"x": 0.5, "y": 0.5})
        swipe = calib.get("swipe_scale", {"x": 1.0, "y": 1.0})
        self.center_status.configure(text=f"Neutral center: x={neutral.get('x', 0.5):.3f}, y={neutral.get('y', 0.5):.3f}")
        self.swipe_status.configure(text=f"Swipe scale: x={swipe.get('x', 1.0):.2f}, y={swipe.get('y', 1.0):.2f}")

        text = [
            "Current tuning:",
            f"- Camera size: {self.cfg.get('camera_width', 960)} x {self.cfg.get('camera_height', 540)}",
            f"- Detection confidence: {self.cfg['detection']['min_detection_confidence']:.2f}",
            f"- Tracking confidence: {self.cfg['detection']['min_tracking_confidence']:.2f}",
            f"- Stable frames: {self.cfg['gesture']['stable_frames']}",
            f"- Cooldown ms: {self.cfg['gesture']['cooldown_ms']}",
            f"- Swipe threshold dx: {self.cfg['gesture']['swipe_min_dx']:.3f}",
            f"- Swipe threshold dy: {self.cfg['gesture']['swipe_min_dy']:.3f}",
            f"- Pinch threshold: {self.cfg['gesture']['pinch_threshold']:.3f}",
        ]
        self.calib_values.delete("1.0", "end")
        self.calib_values.insert("1.0", "\n".join(text))

    def _set_status(self, text: str):
        self.status_pill.configure(text=text)
        self.latest_status["ui_status"] = text

    def toggle_camera(self):
        if self.preview_running:
            self.stop_camera()
        else:
            self.start_camera()

    def start_camera(self):
        if self.preview_running:
            return
        
        if not MEDIAPIPE_AVAILABLE:
            messagebox.showerror(
                "MediaPipe Error",
                "MediaPipe is not properly installed.\n\n"
                "Install with: pip install -r requirements.txt"
            )
            self._set_status("MediaPipe not available")
            return
        
        self.cfg = self._gather_ui_to_config()
        save_config(self.cfg)
        self.stop_event.clear()
        self.preview_running = True
        self.start_btn.configure(text="Camera Running")
        self._set_status("Starting camera...")
        self.capture_thread = threading.Thread(target=self._camera_loop, daemon=True)
        self.capture_thread.start()

    def stop_camera(self):
        self.stop_event.set()
        self.preview_running = False
        self.start_btn.configure(text="Start Camera")
        self._set_status("Camera stopped")
        if self.video_capture is not None:
            try:
                self.video_capture.release()
            except Exception:
                pass
            self.video_capture = None

    def restore_defaults(self):
        if not messagebox.askyesno("Restore Defaults", "Restore all settings to defaults?"):
            return
        self.cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        save_config(self.cfg)
        self._load_ui_from_config()
        self._set_status("Defaults restored")

    def reload_config(self):
        self.cfg = load_config()
        self._load_ui_from_config()
        self._set_status("Config reloaded")

    def save_config(self):
        self.cfg = self._gather_ui_to_config()
        save_config(self.cfg)
        self._refresh_calibration_texts()
        self._set_status("Config saved")

    def reset_calibration(self):
        self.cfg["calibration"] = json.loads(json.dumps(DEFAULT_CONFIG["calibration"]))
        save_config(self.cfg)
        self._refresh_calibration_texts()
        self._set_status("Calibration reset")

    def capture_center_calibration(self):
        self.calibration_state["capture_center"] = True
        self.calibration_state["center_samples"] = []
        self.calib_status.configure(text="Capture center: hold a neutral hand pose for 1.5s")
        self._set_status("Capturing neutral center...")

    def capture_swipe_calibration(self):
        self.calibration_state["capture_swipe"] = True
        self.calibration_state["swipe_samples"] = []
        self.calibration_state["countdown"] = 2.0
        self.calib_status.configure(text="Capture swipe: do one fast swipe now")
        self._set_status("Capturing swipe sample...")

    def _record_event(self, gesture: str, hand: str, status: str, action: str = "", error: str = ""):
        entry = {
            "ts": time.time(),
            "gesture": gesture,
            "hand": hand,
            "status": status,
            "action": action,
            "error": error,
        }
        self.events.insert(0, entry)
        self.events = self.events[:30]
        self._refresh_event_log()

    def _refresh_event_log(self):
        lines = []
        for item in self.events[:20]:
            stamp = time.strftime("%H:%M:%S", time.localtime(item["ts"]))
            if item["status"] == "triggered":
                lines.append(f"[{stamp}] {item['hand']} • {item['gesture']} -> {item.get('action', '')}")
            elif item["status"] == "ignored":
                lines.append(f"[{stamp}] {item['hand']} • {item['gesture']} (ignored: {item.get('action', '')})")
            else:
                lines.append(f"[{stamp}] {item['hand']} • {item['gesture']} (error: {item.get('error', '')})")
        self.events_text.configure(state="normal")
        self.events_text.delete("1.0", "end")
        self.events_text.insert("1.0", "\n".join(lines))
        self.events_text.configure(state="disabled")

    def _create_landmarker(self):
        """Create MediaPipe hand landmarker with error handling for Python 3.14+"""
        ensure_model_downloaded()
        
        BaseOptions = mp.tasks.BaseOptions
        HandLandmarker = mp.tasks.vision.HandLandmarker
        HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
        RunningMode = mp.tasks.vision.RunningMode

        det = self.cfg["detection"]
        options = HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(MODEL_PATH)),
            running_mode=RunningMode.VIDEO,
            num_hands=int(det.get("max_num_hands", 2)),
            min_hand_detection_confidence=float(det.get("min_detection_confidence", 0.82)),
            min_hand_presence_confidence=float(det.get("min_tracking_confidence", 0.78)),
            min_tracking_confidence=float(det.get("min_tracking_confidence", 0.78)),
        )
        return HandLandmarker.create_from_options(options)

    def _camera_loop(self):
        cfg = load_config()
        self.cfg = cfg
        width = int(cfg.get("camera_width", 960))
        height = int(cfg.get("camera_height", 540))
        camera_index = int(cfg.get("camera_index", 0))

        backends = []
        if platform.system().lower().startswith("win"):
            backends.append(cv2.CAP_DSHOW)
        backends.append(0)

        cap = None
        for backend in backends:
            try:
                cap = cv2.VideoCapture(camera_index, backend)
                if cap is not None and cap.isOpened():
                    break
            except Exception:
                continue

        if cap is None or not cap.isOpened():
            self.preview_running = False
            self.after(0, lambda: self._set_status("Camera failed to open"))
            self.after(0, lambda: messagebox.showerror("Camera Error", "Could not open the laptop camera."))
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        self.video_capture = cap

        try:
            landmarker = self._create_landmarker()
        except Exception as exc:
            import traceback

            print("\n====== MEDIAPIPE INITIALIZATION FAILED ======")
            traceback.print_exc()
            print("=" * 50 + "\n")

            try:
                cap.release()
            except Exception:
                pass

            self.video_capture = None
            self.preview_running = False

            err = str(exc)
            
            # Check if it's the Python 3.14 ctypes issue
            if "free" in err and "ctypes" in err:
                err = ("MediaPipe Python 3.14 Compatibility Issue\n\n"
                       "The app has patched MediaPipe automatically, but the issue persists.\n\n"
                       "SOLUTION: Downgrade to Python 3.13\n"
                       "Get it from: https://www.python.org/downloads/release/python-3130/")

            self.after(0, lambda err=err: messagebox.showerror("MediaPipe Error", err))
            return
        
        self.hand_landmarker = landmarker
        self.after(0, lambda: self._set_status("Camera running"))

        prev_time = time.time()

        while not self.stop_event.is_set():
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.01)
                continue

            if cfg.get("preview_flip", True):
                frame = cv2.flip(frame, 1)

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

            timestamp_ms = int(time.time() * 1000)
            if timestamp_ms <= self.frame_ts_ms:
                timestamp_ms = self.frame_ts_ms + 1
            self.frame_ts_ms = timestamp_ms

            try:
                results = landmarker.detect_for_video(mp_image, timestamp_ms)
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda err=err: self._set_status(f"Detection error: {err}"))
                continue

            output = frame.copy()
            hand_count = 0
            last_gesture = "—"

            hand_landmarks_list = getattr(results, "hand_landmarks", []) or []
            handedness_list = getattr(results, "handedness", []) or []

            if hand_landmarks_list:
                hand_count = len(hand_landmarks_list)

                for idx, hand_landmarks in enumerate(hand_landmarks_list):
                    handedness = "Right"
                    if idx < len(handedness_list) and handedness_list[idx]:
                        try:
                            handedness = get_category_label(handedness_list[idx][0]) or "Right"
                        except Exception:
                            pass

                    landmarks = hand_landmarks

                    draw_hand_skeleton(output, landmarks)

                    gesture = classify_gesture(landmarks, handedness, cfg)
                    center = landmarks_center(landmarks)

                    if self.calibration_state["capture_center"]:
                        self.calibration_state["center_samples"].append(center)
                    if self.calibration_state["capture_swipe"]:
                        self.calibration_state["swipe_samples"].append({
                            "t": time.time(),
                            "x": center["x"],
                            "y": center["y"],
                        })

                    label = f"{handedness} hand"
                    self.hand_histories.setdefault(label, deque(maxlen=18)).append(
                        HandSample(t=time.time(), x=center["x"], y=center["y"])
                    )

                    swipe = self._detect_swipe(label, cfg)
                    final_gesture = swipe or gesture

                    if final_gesture:
                        last_gesture = final_gesture
                        self._trigger_gesture(final_gesture, label, cfg)

                    guide_x = int(center["x"] * output.shape[1]) + 12
                    guide_y = int(center["y"] * output.shape[0]) - 12
                    draw_text_box(
                        output,
                        f"{label}: {final_gesture.replace('_', ' ') if final_gesture else 'tracking'}",
                        guide_x,
                        guide_y,
                    )

            self._draw_center_guide(output)

            now = time.time()
            dt = max(now - prev_time, 1e-6)
            prev_time = now
            fps = 1.0 / dt
            self.current_fps = 0.85 * self.current_fps + 0.15 * fps if self.current_fps else fps

            self.latest_status = {
                "hand_count": hand_count,
                "gesture": last_gesture,
                "fps": self.current_fps,
                "status": "Tracking live",
            }

            # Calibration completions
            if self.calibration_state["capture_center"] and len(self.calibration_state["center_samples"]) >= 20:
                avg_x = sum(s["x"] for s in self.calibration_state["center_samples"]) / len(self.calibration_state["center_samples"])
                avg_y = sum(s["y"] for s in self.calibration_state["center_samples"]) / len(self.calibration_state["center_samples"])
                self.cfg["calibration"]["neutral_center"] = {"x": round(avg_x, 4), "y": round(avg_y, 4)}
                self.calibration_state["capture_center"] = False
                self.calibration_state["center_samples"].clear()
                save_config(self.cfg)
                self.after(0, lambda: self._set_status("Neutral center captured"))
                self.after(0, self._refresh_calibration_texts)
                self.after(0, lambda: self.calib_status.configure(text="Neutral center captured successfully"))

            if self.calibration_state["capture_swipe"]:
                self.calibration_state["countdown"] = max(0.0, self.calibration_state["countdown"] - dt)
                if len(self.calibration_state["swipe_samples"]) >= 6 and self.calibration_state["countdown"] <= 0.0:
                    samples = self.calibration_state["swipe_samples"]
                    dx = abs(samples[-1]["x"] - samples[0]["x"])
                    dy = abs(samples[-1]["y"] - samples[0]["y"])
                    self.cfg["gesture"]["swipe_min_dx"] = round(clamp(dx * 0.75, 0.08, 0.30), 3)
                    self.cfg["gesture"]["swipe_min_dy"] = round(clamp(dy * 0.75, 0.08, 0.30), 3)
                    self.calibration_state["capture_swipe"] = False
                    self.calibration_state["swipe_samples"].clear()
                    save_config(self.cfg)
                    self.after(0, lambda: self._set_status("Swipe calibrated"))
                    self.after(0, self._refresh_calibration_texts)
                    self.after(0, lambda: self.calib_status.configure(text="Swipe calibration saved"))

            with self.latest_frame_lock:
                self.latest_pil_image = Image.fromarray(cv2.cvtColor(output, cv2.COLOR_BGR2RGB))
                self.latest_status["hand_count"] = hand_count
                self.latest_status["gesture"] = last_gesture
                self.latest_status["fps"] = self.current_fps

        try:
            landmarker.close()
        except Exception:
            pass
        try:
            cap.release()
        except Exception:
            pass
        self.video_capture = None
        self.hand_landmarker = None

    def _draw_center_guide(self, frame):
        h, w = frame.shape[:2]
        cx, cy = w // 2, h // 2
        box_w = int(w * 0.90)
        box_h = int(h * 0.90)
        x1, y1 = cx - box_w // 2, cy - box_h // 2
        x2, y2 = cx + box_w // 2, cy + box_h // 2
        overlay = frame.copy()
        cv2.rectangle(overlay, (x1, y1), (x2, y2), (34, 197, 94), -1)
        frame[:] = cv2.addWeighted(overlay, 0.08, frame, 0.92, 0)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (34, 197, 94), 2)
        draw_text_box(frame, "Keep hands inside this guide area for calibration", x1 + 10, y1 - 10)

    def _detect_swipe(self, label: str, cfg: Dict[str, Any]) -> Optional[str]:
        history = self.hand_histories.get(label)
        if not history or len(history) < 6:
            return None

        gesture_cfg = cfg["gesture"]
        min_dx = float(gesture_cfg["swipe_min_dx"])
        min_dy = float(gesture_cfg["swipe_min_dy"])
        min_ms = int(gesture_cfg["swipe_min_duration_ms"])
        max_ms = int(gesture_cfg["swipe_max_duration_ms"])

        recent = history[-1]
        for older in list(history)[:-1]:
            dt_ms = (recent.t - older.t) * 1000.0
            if dt_ms < min_ms or dt_ms > max_ms:
                continue

            dx = recent.x - older.x
            dy = recent.y - older.y
            abs_x = abs(dx)
            abs_y = abs(dy)

            if abs_y > min_dy and abs_y > abs_x * 1.15:
                return "swipe_up" if dy < 0 else "swipe_down"

            if abs_x > min_dx and abs_x > abs_y * 1.15:
                return "swipe_left" if dx < 0 else "swipe_right"

        return None

    def _should_fire(self, gesture: str, cfg: Dict[str, Any]) -> bool:
        now = time.time() * 1000.0
        cooldown = int(cfg["gesture"]["cooldown_ms"])
        last = self.last_trigger_at.get(gesture, 0.0)
        if now - last < cooldown:
            return False
        self.last_trigger_at[gesture] = now
        return True

    def _stable_gesture_pass(self, label: str, gesture: Optional[str], cfg: Dict[str, Any]) -> bool:
        if gesture is None:
            self.stable_gesture[label] = None
            self.stable_count[label] = 0
            return False

        prev = self.stable_gesture.get(label)
        if prev == gesture:
            self.stable_count[label] = self.stable_count.get(label, 0) + 1
        else:
            self.stable_gesture[label] = gesture
            self.stable_count[label] = 1

        return self.stable_count.get(label, 0) >= int(cfg["gesture"]["stable_frames"])

    def _trigger_gesture(self, gesture: str, label: str, cfg: Dict[str, Any]):
        binding = cfg.get("bindings", {}).get(gesture)
        if not binding or not binding.get("enabled", False):
            return

        if not self._stable_gesture_pass(label, gesture, cfg):
            return

        if not self._should_fire(gesture, cfg):
            return

        try:
            action = execute_binding(binding)
            self.after(0, lambda: self._record_event(gesture, label, "triggered", action=action))
            self.after(0, lambda: self.lbl_last_gesture.configure(text=f"Last gesture: {gesture.replace('_', ' ')}"))
        except Exception as exc:
            err = str(exc)
            self.after(0, lambda err=err: self._record_event(gesture, label, "error", error=err))
            self.after(0, lambda err=err: self._set_status(f"Gesture error: {err}"))

    def _schedule_ui_updates(self):
        self.after(40, self._ui_tick)

    def _ui_tick(self):
        with self.latest_frame_lock:
            pil_img = self.latest_pil_image.copy() if self.latest_pil_image is not None else None
            status = dict(self.latest_status)

        if pil_img is not None:
            self._show_preview_image(pil_img)

        hand_count = status.get("hand_count", 0)
        gesture = status.get("gesture", "—")
        fps = status.get("fps", 0.0)

        self.lbl_hand_count.configure(text=f"Hands: {hand_count}")
        self.lbl_last_gesture.configure(text=f"Last gesture: {gesture.replace('_', ' ') if gesture and gesture != '—' else '—'}")
        self.lbl_track_status.configure(text=f"Status: {'tracking' if self.preview_running else 'idle'}")
        self.lbl_fps.configure(text=f"FPS: {fps:.1f}" if fps else "FPS: 0")

        if self.calibration_state["capture_swipe"]:
            self.calib_status.configure(text=f"Capture swipe: perform the swipe now ({self.calibration_state['countdown']:.1f}s left)")
            self._refresh_live_feedback()

        self.after(40, self._ui_tick)

    def _refresh_live_feedback(self):
        lines = []
        lines.append("Live detection is active.")
        lines.append(f"Stable frames needed: {self.cfg['gesture']['stable_frames']}")
        lines.append(f"Swipe dx threshold: {self.cfg['gesture']['swipe_min_dx']:.3f}")
        lines.append(f"Swipe dy threshold: {self.cfg['gesture']['swipe_min_dy']:.3f}")
        lines.append(f"Pinch threshold: {self.cfg['gesture']['pinch_threshold']:.3f}")
        lines.append(f"Cooldown ms: {self.cfg['gesture']['cooldown_ms']}")
        self.live_feedback.delete("1.0", "end")
        self.live_feedback.insert("1.0", "\n".join(lines))

    def _show_preview_image(self, pil_img: Image.Image):
        canvas = self.preview_canvas
        canvas.update_idletasks()
        cw = max(1, canvas.winfo_width())
        ch = max(1, canvas.winfo_height())
        img = pil_img.copy()
        img.thumbnail((cw, ch), Image.Resampling.LANCZOS)
        bg = Image.new("RGB", (cw, ch), "#02070c")
        x = (cw - img.width) // 2
        y = (ch - img.height) // 2
        bg.paste(img, (x, y))
        self.tk_preview_img = ImageTk.PhotoImage(bg)
        canvas.delete("all")
        canvas.create_image(cw // 2, ch // 2, image=self.tk_preview_img)
        box_w = int(cw * 0.50)
        box_h = int(ch * 0.50)
        x1, y1 = cw // 2 - box_w // 2, ch // 2 - box_h // 2
        x2, y2 = cw // 2 + box_w // 2, ch // 2 + box_h // 2
        canvas.create_rectangle(x1, y1, x2, y2, outline="#22c55e", width=2)
        canvas.create_text(cw // 2, y1 - 12, text="Center guide", fill="#caffd7", font=("Segoe UI", 10, "bold"))

    def on_close(self):
        self.stop_camera()
        self.destroy()


def main():
    app = GestureApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()