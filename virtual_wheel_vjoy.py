import tkinter as tk
from tkinter import ttk
import math
import time
import threading
import ctypes
from ctypes import wintypes
import os
import sys
import queue
import configparser
import struct
import shutil
import traceback

try:
    import XInput
except Exception as _xinput_error:
    XInput = None
    _XINPUT_IMPORT_ERROR = _xinput_error
else:
    _XINPUT_IMPORT_ERROR = None

# ---------------------------------------------------------
# FATAL STARTUP DIALOGS
# ---------------------------------------------------------
def _fatal_dialog(title, message):
    """Show a clear Win32 error even before Tk has created a window."""
    try:
        ctypes.windll.user32.MessageBoxW(None, str(message), str(title), 0x10)
    except Exception:
        pass


if XInput is None:
    _fatal_dialog(
        "XInput-Python is missing",
        "The XInput Python module could not be imported.\n\n"
        f"{_XINPUT_IMPORT_ERROR}\n\n"
        "Install it with:  python -m pip install XInput-Python"
    )
    raise SystemExit(1)


# ---------------------------------------------------------
# HIGH-PRECISION MULTIMEDIA TIMER SETUP (1ms granularity)
# ---------------------------------------------------------
try:
    ctypes.windll.winmm.timeBeginPeriod(1)
except Exception:
    pass

# ---------------------------------------------------------
# DIRECT 64-BIT WIN32 VJOY DLL BINDINGS (PYTHON 3.14 SAFE)
# ---------------------------------------------------------
def _find_vjoy_dll():
    """Find the installed Brunner/stock vJoy DLL without requiring a local copy."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))

    candidates = [
        os.environ.get("VJOY_DLL"),
        os.path.join(base, "vJoyInterface.dll"),
        os.path.join(base, "vJoy", "x64", "vJoyInterface.dll"),
        r"C:\Program Files\vJoy\x64\vJoyInterface.dll",
        r"C:\Program Files (x86)\vJoy\x64\vJoyInterface.dll",
        "vJoyInterface.dll",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        if candidate == "vJoyInterface.dll" or os.path.exists(candidate):
            return candidate
    return None


vjoy_dll_path = _find_vjoy_dll()
if not vjoy_dll_path:
    _fatal_dialog(
        "vJoy driver not found",
        "vJoyInterface.dll was not found.\n\n"
        "Install the Brunner vJoy driver, then start this app again.\n"
        "Expected path:\nC:\\Program Files\\vJoy\\x64\\vJoyInterface.dll"
    )
    raise SystemExit(1)

try:
    vjoy = ctypes.WinDLL(vjoy_dll_path)
except OSError as exc:
    _fatal_dialog(
        "vJoy driver could not be loaded",
        f"Could not load:\n{vjoy_dll_path}\n\n{exc}\n\n"
        "Reinstall the vJoy driver and make sure the 64-bit DLL is present."
    )
    raise SystemExit(1)

_required_vjoy_exports = (
    "vJoyEnabled", "GetVJDStatus", "AcquireVJD", "RelinquishVJD",
    "SetAxis", "SetBtn", "SetContPov", "IsDeviceFfb",
    "FfbRegisterGenCB", "Ffb_h_Type", "FfbStart", "FfbStop",
)
_missing_vjoy_exports = [name for name in _required_vjoy_exports if not hasattr(vjoy, name)]
if _missing_vjoy_exports:
    _fatal_dialog(
        "Incompatible vJoy driver",
        "The installed vJoyInterface.dll is missing required FFB exports:\n\n"
        + ", ".join(_missing_vjoy_exports)
        + "\n\nInstall the BrunnerInnovation vJoy fork (64-bit), not stock vJoy."
    )
    raise SystemExit(1)

vjoy.vJoyEnabled.restype = wintypes.BOOL
vjoy.GetVJDStatus.argtypes = [wintypes.UINT]
vjoy.GetVJDStatus.restype = ctypes.c_int

vjoy.AcquireVJD.argtypes = [wintypes.UINT]
vjoy.AcquireVJD.restype = wintypes.BOOL

vjoy.RelinquishVJD.argtypes = [wintypes.UINT]
vjoy.RelinquishVJD.restype = None

vjoy.SetAxis.argtypes = [wintypes.LONG, wintypes.UINT, wintypes.UINT]
vjoy.SetAxis.restype = wintypes.BOOL

vjoy.SetBtn.argtypes = [wintypes.BOOL, wintypes.UINT, ctypes.c_ubyte]
vjoy.SetBtn.restype = wintypes.BOOL

vjoy.SetContPov.argtypes = [wintypes.DWORD, wintypes.UINT, ctypes.c_ubyte]
vjoy.SetContPov.restype = wintypes.BOOL

vjoy.IsDeviceFfb.argtypes = [wintypes.UINT]
vjoy.IsDeviceFfb.restype = wintypes.BOOL

for _name, _argtypes, _restype in (
    ("FfbStart", [wintypes.UINT], wintypes.BOOL),
    # vJoyInterface.h declares FfbStop as VOID. Declaring BOOL here is wrong.
    ("FfbStop", [wintypes.UINT], None),
    ("GetVJDContPovNumber", [wintypes.UINT], ctypes.c_int),
    ("GetVJDButtonNumber", [wintypes.UINT], ctypes.c_int),
    ("GetVJDAxisExist", [wintypes.UINT, wintypes.UINT], wintypes.BOOL),
):
    try:
        _fn = getattr(vjoy, _name)
        _fn.argtypes = _argtypes
        _fn.restype = _restype
    except AttributeError:
        pass


class FFB_DATA(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_ulong),
        ("cmd", ctypes.c_ulong),
        ("data", ctypes.c_void_p),
    ]


class JOYSTICK_POSITION_V3(ctypes.Structure):
    """Brunner V3 JOYSTICK_POSITION. UpdateVJD copies this exact 124-byte layout."""
    _fields_ = [
        ("bDevice", ctypes.c_ubyte),
        ("wThrottle", ctypes.c_long),
        ("wRudder", ctypes.c_long),
        ("wAileron", ctypes.c_long),
        ("wAxisX", ctypes.c_long),
        ("wAxisY", ctypes.c_long),
        ("wAxisZ", ctypes.c_long),
        ("wAxisXRot", ctypes.c_long),
        ("wAxisYRot", ctypes.c_long),
        ("wAxisZRot", ctypes.c_long),
        ("wSlider", ctypes.c_long),
        ("wDial", ctypes.c_long),
        ("wWheel", ctypes.c_long),
        ("wAccelerator", ctypes.c_long),
        ("wBrake", ctypes.c_long),
        ("wClutch", ctypes.c_long),
        ("wSteering", ctypes.c_long),
        ("wAxisVX", ctypes.c_long),
        ("wAxisVY", ctypes.c_long),
        ("lButtons", ctypes.c_long),
        ("bHats", wintypes.DWORD),
        ("bHatsEx1", wintypes.DWORD),
        ("bHatsEx2", wintypes.DWORD),
        ("bHatsEx3", wintypes.DWORD),
        ("lButtonsEx1", ctypes.c_long),
        ("lButtonsEx2", ctypes.c_long),
        ("lButtonsEx3", ctypes.c_long),
        ("wAxisVZ", ctypes.c_long),
        ("wAxisVBRX", ctypes.c_long),
        ("wAxisVBRY", ctypes.c_long),
        ("wAxisVBRZ", ctypes.c_long),
    ]


if hasattr(vjoy, "UpdateVJD"):
    vjoy.UpdateVJD.argtypes = [wintypes.UINT, ctypes.POINTER(JOYSTICK_POSITION_V3)]
    vjoy.UpdateVJD.restype = wintypes.BOOL

vjoy.Ffb_h_Type.argtypes = [ctypes.POINTER(FFB_DATA), ctypes.POINTER(ctypes.c_int)]
vjoy.Ffb_h_Type.restype = wintypes.DWORD

FFB_CB_TYPE = ctypes.WINFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)
vjoy.FfbRegisterGenCB.argtypes = [FFB_CB_TYPE, ctypes.c_void_p]
vjoy.FfbRegisterGenCB.restype = None

HID_USAGE_X = 0x30
HID_USAGE_Y = 0x31
HID_USAGE_Z = 0x32
HID_USAGE_RX = 0x33
HID_USAGE_RY = 0x34

VJD_STAT_OWN = 0
VJD_STAT_FREE = 1
VJD_STAT_BUSY = 2
VJD_STAT_MISS = 3

# DirectInput PID report IDs (Brunner vJoy layouts).
PT_EFFREP = 0x01
PT_ENVREP = 0x02
PT_CONDREP = 0x03
PT_PRIDREP = 0x04
PT_CONSTREP = 0x05
PT_RAMPREP = 0x06
PT_CSTMREP = 0x07
PT_SMPLREP = 0x08
PT_EFOPREP = 0x0A
PT_BLKFRREP = 0x0B
PT_CTRLREP = 0x0C
PT_GAINREP = 0x0D
PT_NEWEFREP = 0x11

ET_CONST = 1
ET_RAMP = 2
ET_SQR = 3
ET_SINE = 4
ET_TRNGL = 5
ET_STUP = 6
ET_STDN = 7
ET_SPRNG = 8
ET_DMPR = 9
ET_INRT = 10
ET_FRCTN = 11
ET_CUSTOM = 12

FILE_MAP_READ = 0x0004
TH32CS_SNAPPROCESS = 0x00000002

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

kernel32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenFileMappingW.restype = wintypes.HANDLE
kernel32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
kernel32.MapViewOfFile.restype = ctypes.c_void_p
kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
kernel32.UnmapViewOfFile.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
kernel32.Process32FirstW.restype = wintypes.BOOL
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
kernel32.Process32NextW.restype = wintypes.BOOL

kernel32.GetConsoleWindow.restype = wintypes.HWND
kernel32.AllocConsole.restype = wintypes.BOOL
kernel32.FreeConsole.restype = wintypes.BOOL

SPI_GETWORKAREA = 0x0030


def _enable_dpi_awareness():
    """Use physical pixels so Windows scaling and RDP cannot clip the layout.

    Without this, Tk may lay out in one coordinate space while DWM scales the
    whole window in another, which is what pushes the bottom behind the
    taskbar on high-DPI or RDP sessions.
    """
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _get_work_area():
    """Return the desktop area excluding the taskbar, including on RDP."""
    rect = wintypes.RECT()
    try:
        if ctypes.windll.user32.SystemParametersInfoW(
                SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
            return (int(rect.left), int(rect.top),
                    int(rect.right), int(rect.bottom))
    except Exception:
        pass
    try:
        width = int(ctypes.windll.user32.GetSystemMetrics(0))
        height = int(ctypes.windll.user32.GetSystemMetrics(1))
    except Exception:
        width, height = 1280, 720
    return 0, 0, width, height


def _draw_app_icon(size=256):
    """Draw the application icon: gate ring, cyan arc, anti-whip wedge, stick.

    Kept in the main module so the tray icon, the window icon and the generated
    .ico all come from the same artwork. Requires Pillow, which is imported
    lazily so the app still starts if the tray dependency is unavailable.
    """
    from PIL import Image, ImageDraw
    import math as _math

    n = max(16, int(size))
    ss = 4  # supersample, then downscale for smooth arcs
    s = n * ss
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def P(value):
        return value * s / 256.0

    def W(value):
        return max(1, int(round(P(value))))

    # Rounded dark tile so the icon reads on light and dark taskbars.
    d.rounded_rectangle(
        (P(6), P(6), P(250), P(250)), radius=P(46),
        fill=(20, 20, 20, 255), outline=(44, 44, 44, 255), width=W(2))

    cx = cy = P(128)
    r = P(86)
    ring = (cx - r, cy - r, cx + r, cy + r)

    # Gate circle and crosshair.
    d.ellipse(ring, outline=(58, 58, 58, 255), width=W(3))
    d.line((cx - r, cy, cx + r, cy), fill=(45, 45, 45, 255), width=W(1.5))
    d.line((cx, cy - r, cx, cy + r), fill=(45, 45, 45, 255), width=W(1.5))

    # Cyan steering arc. Pillow measures clockwise from 3 o'clock; 115..425
    # leaves a 50 degree gap at the bottom (about 155 degrees each side of
    # top), matching the gate range in the app.
    d.arc(ring, start=115, end=425, fill=(0, 229, 255, 255), width=W(10))

    # Purple anti-whip wedge at the bottom. A faint annular fill keeps the
    # wedge readable at small icon sizes; the outlines match the live gate.
    inner_r = P(52)
    inner_ring = (cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r)
    wedge_points = []
    for angle_deg in range(65, 116):
        rad = _math.radians(angle_deg)
        wedge_points.append((cx + _math.cos(rad) * r, cy + _math.sin(rad) * r))
    for angle_deg in range(115, 64, -1):
        rad = _math.radians(angle_deg)
        wedge_points.append(
            (cx + _math.cos(rad) * inner_r, cy + _math.sin(rad) * inner_r))
    d.polygon(wedge_points, fill=(52, 0, 52, 255))
    d.arc(ring, start=65, end=115, fill=(255, 0, 255, 255), width=W(4))
    d.arc(inner_ring, start=65, end=115, fill=(255, 0, 255, 255), width=W(3))
    for angle_deg in (65, 115):
        rad = _math.radians(angle_deg)
        d.line(
            (cx + _math.cos(rad) * inner_r, cy + _math.sin(rad) * inner_r,
             cx + _math.cos(rad) * r, cy + _math.sin(rad) * r),
            fill=(255, 0, 255, 255), width=W(3))

    # Stick lever pointing up and to the right, with a yellow cap.
    stick_angle = _math.radians(-64)
    tip = (cx + _math.cos(stick_angle) * P(50),
           cy + _math.sin(stick_angle) * P(50))
    d.line((cx, cy, tip[0], tip[1]), fill=(24, 24, 24, 255), width=W(13))
    d.line((cx, cy, tip[0], tip[1]), fill=(150, 150, 150, 255), width=W(6))

    pivot_r = P(11)
    d.ellipse((cx - pivot_r, cy - pivot_r, cx + pivot_r, cy + pivot_r),
              fill=(24, 24, 24, 255), outline=(190, 190, 190, 255), width=W(2))
    knob_r = P(14)
    d.ellipse((tip[0] - knob_r, tip[1] - knob_r,
               tip[0] + knob_r, tip[1] + knob_r),
              fill=(255, 204, 0, 255), outline=(255, 255, 255, 255), width=W(2))
    highlight = P(4)
    d.ellipse((tip[0] - knob_r * 0.45 - highlight,
               tip[1] - knob_r * 0.45 - highlight,
               tip[0] - knob_r * 0.45 + highlight,
               tip[1] - knob_r * 0.45 + highlight),
              fill=(255, 255, 255, 120))

    return img.resize((n, n), Image.LANCZOS)


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _is_process_running(names):
    """Return True when one of the supplied executable names is running."""
    wanted = {str(name).lower() for name in names}
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if not snapshot or int(snapshot) == invalid_handle:
        return False
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return False
        while True:
            if entry.szExeFile.lower() in wanted:
                return True
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                return False
    finally:
        kernel32.CloseHandle(snapshot)
    return False


class SharedMemoryReader:
    """Read-only named shared-memory mapping. Never creates AC's mappings."""
    def __init__(self, name, size):
        self.name = name
        self.size = int(size)
        self._handle = None
        self._ptr = None

    @property
    def is_open(self):
        return self._ptr is not None

    def open(self):
        self.close()
        handle = kernel32.OpenFileMappingW(FILE_MAP_READ, False, self.name)
        if not handle:
            return False
        pointer = kernel32.MapViewOfFile(handle, FILE_MAP_READ, 0, 0, self.size)
        if not pointer:
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        self._ptr = int(pointer)
        return True

    def close(self):
        if self._ptr is not None:
            try:
                kernel32.UnmapViewOfFile(self._ptr)
            except Exception:
                pass
            self._ptr = None
        if self._handle is not None:
            try:
                kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None

    def read(self, offset, fmt):
        if self._ptr is None:
            raise RuntimeError("shared memory is not open")
        size = struct.calcsize(fmt)
        if offset < 0 or offset + size > self.size:
            raise ValueError("shared-memory read is outside the mapping")
        return struct.unpack(fmt, ctypes.string_at(self._ptr + offset, size))


def _is_writable_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".vw_write_test")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("")
        os.remove(probe)
        return True
    except Exception:
        return False


def _app_data_dir():
    """Prefer the folder containing the EXE/script; fall back only if read-only."""
    if getattr(sys, "frozen", False):
        preferred = os.path.dirname(os.path.abspath(sys.executable))
    else:
        preferred = os.path.dirname(os.path.abspath(__file__))
    if _is_writable_dir(preferred):
        return preferred
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    fallback = os.path.join(base, "VirtualWheel")
    try:
        os.makedirs(fallback, exist_ok=True)
    except Exception:
        pass
    return fallback


APP_DIR = _app_data_dir()
SETTINGS_FILE = os.path.join(APP_DIR, "settings.ini")
LEGACY_APPDATA_SETTINGS_FILE = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"),
    "VirtualWheel",
    "settings.ini",
)
if getattr(sys, "frozen", False):
    if os.path.abspath(APP_DIR) == os.path.abspath(os.path.dirname(os.path.abspath(sys.executable))):
        # Migrate the old per-user location to the folder beside the EXE.
        LEGACY_SETTINGS_FILE = LEGACY_APPDATA_SETTINGS_FILE
    else:
        # EXE folder was read-only; use APPDATA and migrate a legacy copy there.
        LEGACY_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "settings.ini")
else:
    LEGACY_SETTINGS_FILE = SETTINGS_FILE

# ---------------------------------------------------------
# MAIN UNIVERSAL WHEEL APPLICATION
# ---------------------------------------------------------
class UniversalWheelApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Universal Virtual Wheel & Gate Arc Engine")
        self.root.resizable(True, True)
        try:
            # Keep a fixed 96-DPI layout grid. Combined with DPI awareness this
            # prevents Tk and Windows from scaling the UI twice on RDP.
            self.root.tk.call("tk", "scaling", 96.0 / 72.0)
        except Exception:
            pass

        # Apply dark mode theme
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TFrame', background='#1a1a1a')
        style.configure('TLabel', background='#1a1a1a', foreground='#ffffff', font=("Segoe UI", 9))
        style.configure('Small.TLabel', background='#1a1a1a', foreground='#d6d6d6', font=("Segoe UI", 8))
        style.configure('Telemetry.TLabel', background='#1a1a1a', foreground='#d6d6d6', font=("Segoe UI", 7, "bold"))
        style.configure('TLabelFrame', background='#1a1a1a', foreground='#00e5ff')
        style.configure('TLabelFrame.Label', background='#1a1a1a', foreground='#00e5ff', font=("Segoe UI", 9, "bold"))
        style.configure('TButton', background='#303030', foreground='#ffffff', font=("Segoe UI", 8), padding=(7, 3))
        style.map('TButton', background=[('active', '#3d3d3d')])
        style.configure('TCheckbutton', background='#1a1a1a', foreground='#ffffff', font=("Segoe UI", 8))
        style.configure('TRadiobutton', background='#1a1a1a', foreground='#ffffff')
        style.configure('TLabelframe', background='#1a1a1a', foreground='#00e5ff')
        style.configure('TLabelframe.Label', background='#1a1a1a', foreground='#00e5ff', font=("Segoe UI", 9, "bold"))
        style.configure('TCombobox', background='#303030', foreground='#ffffff', fieldbackground='#303030', font=("Segoe UI", 8), padding=(4, 2))
        style.map('TCombobox', fieldbackground=[('readonly', '#303030')])
        style.configure('Horizontal.TScale', background='#1a1a1a', troughcolor='#252525')
        style.configure('Vertical.TScale', background='#1a1a1a', troughcolor='#252525')

        self.root.configure(bg='#1a1a1a')

        # Canvas layout is recomputed from the live widget size so the app can
        # follow RDP resolution changes and user resizes without clipping.
        self._last_work_area = None
        self._work_area_check = 0.0
        self._gate_size = None
        self._curve_size = None
        self._wheel_size = None
        self._ctrl_size = None
        self._ctrl_geom = {}
        self._ctrl_last_state = ({}, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        self._reflow_job = None

        # GUI pacing. The window renders at 30 Hz while it is in the foreground,
        # drops to a 5 Hz idle rate after a couple of seconds in the background,
        # and stops drawing while minimized or hidden in the tray.
        self._foreground_hwnd = None
        self._unfocused_since = None
        self._idle_mode = False
        self._last_draw_state = None
        self._gui_tick_count = 0
        self._gui_tick_last = 0.0
        self._gui_tick_gaps = []

        # Tray icon. The menu runs on a pystray thread, so actions are queued
        # and executed by the Tk main loop instead of touching widgets directly.
        self._ui_queue = queue.SimpleQueue()
        self._tray_icon = None
        self._tray_thread = None
        self._minimize_to_tray = False
        self._tray_warned = False

        # App/thread state
        self.running = False
        self._closing = False
        self.current_wheel_pos = 0.0
        self.active_controller_index = 0
        self.vjoy_id = 1
        self.vjoy_acquired = False
        self._vjoy_batch_supported = hasattr(vjoy, "UpdateVJD")
        self._vjoy_button_count = 0
        self._vjoy_pov_count = 0
        self._vjoy_missing_axes = []
        self._vjoy_ffb_supported = False
        self._ffb_started = False
        self._ffb_start_ok = False
        self._initial_status_text = "vJoy Device 1 not acquired"
        self._initial_status_color = "#ff6666"

        self._stop_event = threading.Event()
        self._worker_thread = None
        self._ffb_stop_event = threading.Event()
        self._ffb_thread = None
        self._ffb_queue = queue.Queue(maxsize=8192)
        self._ffb_lock = threading.RLock()
        self._ac_lock = threading.RLock()
        self._deconflict_lock = threading.Lock()

        # FFB state. All mutation is performed by the FFB worker or under _ffb_lock.
        self.current_ffb_torque = 0.0
        self.raw_ffb_magnitude = 0.0
        self.ffb_direction_deg = 0.0
        self._device_gain = 1.0
        self._ffb_actuators_enabled = True
        self._ffb_paused = False
        self._ffb_blocks = {}
        self._ffb_last_packet_time = 0.0
        self._ffb_last_type = 0
        self._ffb_last_block = None
        self._ffb_packet_count = 0
        self._ffb_rate = 0.0
        self._ffb_rate_window_start = time.perf_counter()
        self._ffb_rate_window_count = 0
        self._ffb_errors = 0
        self._ffb_queue_drops = 0
        self._debug_enabled = False
        self._debug_seen = set()
        self._debug_last_print = 0.0
        self._worker_errors = 0
        self._last_worker_error_time = 0.0
        self._last_ffb_label_source = None

        # Raw FFB capture (diagnostic file, 10 seconds by default)
        self._ffb_capture_until = 0.0
        self._ffb_capture_path = None
        self._ffb_capture_file = None
        self._ffb_capture_count = 0
        self._capture_done_path = None
        self._capture_error = None

        # AC shared memory is opened read-only and validated against the AC process.
        self.ac_graphics_shm = None
        self.ac_physics_shm = None
        self.ac_shm_last_retry = 0.0
        self._ac_process_check = 0.0

        # Test rumble state
        self.test_rumble_until = 0.0
        self.test_heavy_val = 0.0
        self.test_light_val = 0.0

        # Bind deconflict mode (1.5 s delay, auto-off after 60 s)
        self.deconflict_mode_var = tk.BooleanVar(value=False)
        self.deconflict_expire_time = 0.0
        self.deconflict_queue = []
        self._deconflict_auto_off = False

        # Anti-whip state
        self.whip_clamped_side = 0
        self._last_gate_deg = None

        # Pedal/button options
        self.invert_throttle_var = tk.BooleanVar(value=False)
        self.invert_brake_var = tk.BooleanVar(value=False)
        self.rs_buttons_var = tk.BooleanVar(value=True)

        # Debug console
        self.ffb_debug_var = tk.BooleanVar(value=False)
        self._console_allocated = False
        self._orig_stdout = None
        self._orig_stderr = None
        self._console_streams = []

        # Status-restore timer
        self.status_restore_timer = None

        # Live controller state and telemetry
        self.live_input_state = {
            "buttons": {},
            "lx": 0.0, "ly": 0.0,
            "rx": 0.0, "ry": 0.0,
            "lt": 0.0, "rt": 0.0
        }
        self.telemetry = {
            "raw_x": 0.0,
            "raw_y": 0.0,
            "unfiltered_steer": 0.0,
            "steer": 0.0,
            "throttle": 0.0,
            "brake": 0.0,
            "ffb_torque": 0.0,
            "heavy_rumble": 0.0,
            "light_rumble": 0.0
        }

        # Standard button mapping (Xbox / GameSir Nova 2 Lite). Buttons 1-18 are
        # used; a 32-button vJoy device is also accepted.
        self.button_map = {
            "A": 1, "B": 2, "X": 3, "Y": 4,
            "LEFT_SHOULDER": 5, "RIGHT_SHOULDER": 6,
            "BACK": 7, "START": 8,
            "LEFT_THUMB": 9, "RIGHT_THUMB": 10,
            "DPAD_UP": 11, "DPAD_DOWN": 12,
            "DPAD_LEFT": 13, "DPAD_RIGHT": 14
        }

        # Runtime snapshot consumed by the process thread. Never read Tk from it.
        self._runtime_cfg = {}
        self._last_tick_time = None
        self._last_wheel_pos = 0.0
        self._last_wheel_vel = 0.0
        self._last_rumble_time = 0.0
        self._last_rumble_values = (0.0, 0.0)
        self._last_axis_values = {}
        self._last_pov_value = None
        self._last_btn_states = {}
        self._controller_connected = False
        self._controller_lost_since = None
        self._controller_neutral_sent = False
        self._controller_scan_time = 0.0
        self._health_last_update = 0.0

        # vJoy comes first, then the callback registration, then the UI.
        self._init_vjoy()
        self._init_ffb_callback()
        self._build_ui()
        self._apply_window_icon()
        self._apply_initial_window_geometry()
        self._load_settings()
        self._snapshot_runtime_config()
        self._update_ffb_source_label()
        self._restore_status_label()
        self.root.bind("<Map>", self._on_root_map, add="+")
        self._init_tray()
        self._start_ffb_worker()
        self._start_gui_loop()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _init_vjoy(self):
        """Acquire device 1 once and cache the device's actual capabilities."""
        try:
            enabled = bool(vjoy.vJoyEnabled())
        except Exception:
            enabled = False
        if not enabled:
            self._initial_status_text = "vJoy driver not enabled (run vJoyConf / reboot)"
            self._initial_status_color = "#ff6666"
            return

        try:
            status = int(vjoy.GetVJDStatus(self.vjoy_id))
        except Exception:
            status = VJD_STAT_MISS

        if status == VJD_STAT_OWN:
            self.vjoy_acquired = True
        elif status == VJD_STAT_FREE:
            try:
                self.vjoy_acquired = bool(vjoy.AcquireVJD(self.vjoy_id))
            except Exception:
                self.vjoy_acquired = False
        else:
            self.vjoy_acquired = False

        if not self.vjoy_acquired:
            if status == VJD_STAT_BUSY:
                self._initial_status_text = "vJoy Device 1 is busy (close other vJoy clients)"
            elif status == VJD_STAT_MISS:
                self._initial_status_text = "vJoy Device 1 is missing (create it in vJoyConf)"
            else:
                self._initial_status_text = "vJoy Device 1 could not be acquired"
            self._initial_status_color = "#ff6666"
            return

        try:
            self._vjoy_button_count = int(vjoy.GetVJDButtonNumber(self.vjoy_id))
        except Exception:
            self._vjoy_button_count = 0
        try:
            self._vjoy_pov_count = int(vjoy.GetVJDContPovNumber(self.vjoy_id))
        except Exception:
            self._vjoy_pov_count = 0

        self._vjoy_missing_axes = []
        try:
            for label, usage in (
                ("X", HID_USAGE_X), ("Y", HID_USAGE_Y), ("Z", HID_USAGE_Z),
                ("Rx", HID_USAGE_RX), ("Ry", HID_USAGE_RY),
            ):
                if not vjoy.GetVJDAxisExist(self.vjoy_id, usage):
                    self._vjoy_missing_axes.append(label)
        except Exception:
            pass

        try:
            self._vjoy_ffb_supported = bool(vjoy.IsDeviceFfb(self.vjoy_id))
        except Exception:
            self._vjoy_ffb_supported = False

        if self._vjoy_ffb_supported and hasattr(vjoy, "FfbStart"):
            try:
                self._ffb_started = bool(vjoy.FfbStart(self.vjoy_id))
            except Exception:
                self._ffb_started = False
        self._ffb_start_ok = self._ffb_started

        bits = []
        if self._vjoy_missing_axes:
            bits.append("missing axes: " + ", ".join(self._vjoy_missing_axes))
        if not self._vjoy_ffb_supported:
            bits.append("FFB not enabled in vJoyConf")
        if not self._ffb_started:
            bits.append("FfbStart failed")
        if bits:
            self._initial_status_text = "vJoy Device 1 acquired — " + "; ".join(bits)
            self._initial_status_color = "#ffcc00"
        else:
            self._initial_status_text = "vJoy Device 1 acquired (FFB ready)"
            self._initial_status_color = "#ffffff"

        # Clear any stale axis/button state left by a previous client.
        self._safe_zero_outputs()

    def _init_ffb_callback(self):
        """The vJoy callback only copies the raw report into a queue.

        It runs on vJoy's hidden-window thread. It must not touch Tk, print at
        packet rate, or call the old FFB helper structs.
        """
        def _ffb_cb(data_ptr, user_data):
            try:
                if not data_ptr:
                    return
                packet = ctypes.cast(data_ptr, ctypes.POINTER(FFB_DATA))
                if not packet.contents.data:
                    return
                pkt_type = ctypes.c_int()
                if vjoy.Ffb_h_Type(packet, ctypes.byref(pkt_type)) != 0:
                    return
                # FFB_DATA.size includes the 8-byte FFB_DATA header. The
                # payload starts at data and has size - 8 valid bytes.
                total_size = int(packet.contents.size)
                if total_size <= 8:
                    return
                payload_len = min(total_size - 8, 256)
                raw = ctypes.string_at(packet.contents.data, payload_len)
                try:
                    self._ffb_queue.put_nowait((time.perf_counter(), int(pkt_type.value), raw))
                except queue.Full:
                    self._ffb_queue_drops += 1
            except Exception:
                self._ffb_errors += 1

        self._ffb_c_cb = FFB_CB_TYPE(_ffb_cb)
        try:
            vjoy.FfbRegisterGenCB(self._ffb_c_cb, None)
            print("[FFB] Callback registered.")
        except Exception as exc:
            print(f"[FFB] Could not register callback: {exc}")

    def _start_ffb_worker(self):
        if self._ffb_thread and self._ffb_thread.is_alive():
            return
        self._ffb_stop_event.clear()
        self._ffb_thread = threading.Thread(target=self._ffb_worker_loop, name="ffb-worker", daemon=True)
        self._ffb_thread.start()

    def _ffb_worker_loop(self):
        while not self._ffb_stop_event.is_set():
            # Block for the first packet instead of polling every 2 ms. A
            # packet wakes the thread immediately, and the 10 ms timeout keeps
            # the rate/capture bookkeeping fresh without 500 idle wakeups/s.
            try:
                first = self._ffb_queue.get(timeout=0.01)
            except queue.Empty:
                self._update_ffb_rate()
                self._update_capture()
                continue

            packets = [first]
            for _ in range(255):
                try:
                    packets.append(self._ffb_queue.get_nowait())
                except queue.Empty:
                    break
            for ts, ptype, raw in packets:
                try:
                    self._handle_ffb_packet(ts, ptype, raw)
                except Exception:
                    self._ffb_errors += 1
                    if self._debug_enabled:
                        print(f"[FFB] Parse error:\n{traceback.format_exc()}")
            self._update_ffb_rate()
            self._update_capture()

    def _update_ffb_rate(self):
        now = time.perf_counter()
        elapsed = now - self._ffb_rate_window_start
        if elapsed >= 1.0:
            self._ffb_rate = self._ffb_rate_window_count / elapsed
            self._ffb_rate_window_count = 0
            self._ffb_rate_window_start = now

    def _get_block_locked(self, block_id):
        block = self._ffb_blocks.get(block_id)
        if block is None:
            block = {
                "id": block_id,
                "type": 0,
                "gain": 1.0,
                "duration_ms": 0xFFFF,
                "start_delay_ms": 0,
                "trigger_btn": 0xFF,
                "playing": True,
                "updated": 0.0,
                "started_at": None,
                "axis_x": True,
                "axis_y": False,
                "polar": False,
                "direction_deg": 0.0,
                "direction_x": 1.0,
                "direction_y": 0.0,
                "constant": None,
                "condition": None,
                "condition_x": None,
                "condition_y": None,
                "periodic": None,
                "ramp": None,
                "periodic_t0": 0.0,
                "ramp_t0": 0.0,
            }
            self._ffb_blocks[block_id] = block
        return block

    @staticmethod
    def _u16(raw, offset):
        return struct.unpack_from("<H", raw, offset)[0]

    @staticmethod
    def _s16(raw, offset):
        return struct.unpack_from("<h", raw, offset)[0]

    @staticmethod
    def _s8(raw, offset):
        return struct.unpack_from("<b", raw, offset)[0]

    @staticmethod
    def _report_block_index(ptype, raw):
        if ptype in (PT_EFFREP, PT_CONDREP, PT_PRIDREP, PT_CONSTREP,
                     PT_RAMPREP, PT_EFOPREP, PT_BLKFRREP) and len(raw) > 1:
            return int(raw[1])
        return None

    def _handle_ffb_packet(self, ts, ptype, raw):
        if not raw:
            return
        self._ffb_packet_count += 1
        self._ffb_rate_window_count += 1
        self._ffb_last_packet_time = ts
        self._ffb_last_type = ptype
        block_index = self._report_block_index(ptype, raw)
        if block_index is not None:
            self._ffb_last_block = block_index

        with self._ffb_lock:
            if ptype == PT_EFFREP:
                self._parse_effect_report_locked(ts, raw)
            elif ptype == PT_CONDREP:
                self._parse_condition_locked(ts, raw)
            elif ptype == PT_CONSTREP:
                self._parse_constant_locked(ts, raw)
            elif ptype == PT_PRIDREP:
                self._parse_periodic_locked(ts, raw)
            elif ptype == PT_RAMPREP:
                self._parse_ramp_locked(ts, raw)
            elif ptype == PT_EFOPREP:
                self._parse_operation_locked(ts, raw)
            elif ptype == PT_BLKFRREP:
                if len(raw) > 1:
                    self._ffb_blocks.pop(raw[1], None)
            elif ptype == PT_CTRLREP:
                self._parse_control_locked(raw)
            elif ptype == PT_GAINREP:
                if len(raw) > 1:
                    self._device_gain = max(0.0, min(1.0, raw[1] / 255.0))
            elif ptype == PT_NEWEFREP:
                # Create New Effect Report carries the effect type in raw[1]
                # and the allocated block ID in raw[2]. It has no effect
                # parameters, so do not create a block here; the following
                # Set Effect Report supplies the block index and parameters.
                pass

        if self._debug_enabled:
            self._debug_ffb_packet(ts, ptype, raw)
        if self._ffb_capture_file is not None:
            self._write_capture(ts, ptype, raw)

    def _parse_effect_report_locked(self, ts, raw):
        # Brunner vJoy Ffb_h_Eff_Report layout (data[] starts at the report ID):
        # [1] block, [2] type, [3:5] duration, [5:7] trigger repeat,
        # [7:9] sample period, [9:11] start delay, [11] gain,
        # [12] trigger button, [13] axes-enable bits 0/1 + polar bit 2,
        # [14:16] polar angle or signed DirX, [16:18] signed DirY.
        # The installed driver is the Brunner fork; stock vJoy's gain at [9]
        # and direction at [12:14] are not valid for this DLL.
        if len(raw) < 18:
            return
        axes = raw[13] & 0x03
        polar = bool(raw[13] & 0x04)
        # Brunner requires the extra DirY word for Cartesian reports that
        # actually select an axis. Do not create a block for a truncated one.
        if not polar and axes and len(raw) < 20:
            return
        block = self._get_block_locked(raw[1])
        old_type = block.get("type", 0)
        old_duration = block.get("duration_ms", 0xFFFF)
        was_playing = bool(block.get("playing", True))
        effect_type = int(raw[2])
        duration_ms = self._u16(raw, 3)
        start_delay_ms = self._u16(raw, 9)
        gain = raw[11] / 255.0
        trigger_btn = int(raw[12])

        is_condition = effect_type in (ET_SPRNG, ET_DMPR, ET_INRT, ET_FRCTN)
        if is_condition:
            # Condition reports carry their own X/Y selection. Ignore the
            # effect report direction for these effects.
            direction_deg = 0.0
            direction_x = 1.0
            direction_y = 0.0
        elif polar:
            direction_raw = min(self._u16(raw, 14), 0x7FFF)
            direction_deg = (direction_raw * 360.0) / 32767.0
            direction_deg %= 360.0
            if axes == 0x03:
                # Two-axis FFB device: honor the reported polar angle.
                angle = math.radians(direction_deg)
                direction_x = math.cos(angle)
                direction_y = math.sin(angle)
            elif axes == 0x02:
                direction_x = 0.0
                direction_y = 1.0
            else:
                # vJoy wheel FFB exposes one force axis. Windows still sends
                # a default polar direction (commonly 90 degrees) even when
                # the game's effect is for X, so direction is not meaningful
                # unless both axes are explicitly enabled.
                direction_x = 1.0
                direction_y = 0.0
        else:
            # Cartesian directions are signed 16-bit values. Some clients
            # send an 18-byte payload with no direction; default to X.
            dir_x = self._s16(raw, 14) if len(raw) >= 18 else 0
            dir_y = self._s16(raw, 16) if len(raw) >= 20 else 0
            if axes == 0x03 and (dir_x or dir_y):
                length = math.hypot(dir_x, dir_y)
                direction_x = dir_x / length
                direction_y = dir_y / length
                direction_deg = math.degrees(math.atan2(dir_y, dir_x)) % 360.0
            elif axes == 0x02:
                direction_x = 0.0
                direction_y = 1.0
                direction_deg = 90.0
            else:
                # Single-axis wheel or unspecified axes: apply to X.
                direction_deg = 0.0
                direction_x = 1.0
                direction_y = 0.0

        if axes:
            axis_x = bool(axes & 0x01)
            axis_y = bool(axes & 0x02)
        else:
            # Most racing titles omit Axes Enable for X-axis effects.
            axis_x = True
            axis_y = False

        condition_types = (ET_SPRNG, ET_DMPR, ET_INRT, ET_FRCTN)
        periodic_types = (ET_SQR, ET_SINE, ET_TRNGL, ET_STUP, ET_STDN)
        if old_type != 0 and old_type != effect_type:
            # A block may be reused for a different effect. Drop the old
            # type-specific parameters so stale forces cannot leak through.
            block["constant"] = None
            block["condition"] = None
            block["condition_x"] = None
            block["condition_y"] = None
            block["periodic"] = None
            block["ramp"] = None
            if old_type == ET_CONST:
                self.raw_ffb_magnitude = 0.0
        elif old_type == 0:
            # Type 0 means the block was just created by a parameter report
            # that arrived before its Set Effect Report. Keep parameters that
            # match the reported effect type, but discard unrelated stale data.
            if effect_type != ET_CONST:
                block["constant"] = None
                self.raw_ffb_magnitude = 0.0
            if effect_type not in condition_types:
                block["condition"] = None
                block["condition_x"] = None
                block["condition_y"] = None
            if effect_type not in periodic_types:
                block["periodic"] = None
            if effect_type != ET_RAMP:
                block["ramp"] = None
        block["type"] = effect_type
        block["duration_ms"] = duration_ms
        block["start_delay_ms"] = start_delay_ms
        block["trigger_btn"] = trigger_btn
        block["gain"] = max(0.0, min(1.0, gain))
        block["axis_x"] = axis_x
        block["axis_y"] = axis_y
        block["polar"] = polar
        block["direction_deg"] = direction_deg
        block["direction_x"] = direction_x
        block["direction_y"] = direction_y
        # A Set Effect Report defines parameters; it does not restart an
        # effect that the game already stopped. Keep a stopped block stopped
        # until an Operation Start report arrives. A brand-new block (type 0)
        # is allowed to play immediately because some titles only send the
        # effect report and never an explicit operation start.
        block["playing"] = True if old_type == 0 else was_playing
        if (old_type != effect_type or old_duration != duration_ms
                or block.get("started_at") is None):
            block["started_at"] = ts
            block["periodic_t0"] = ts + start_delay_ms / 1000.0
            block["ramp_t0"] = ts + start_delay_ms / 1000.0
        block["updated"] = ts
        self.ffb_direction_deg = direction_deg

    def _parse_condition_locked(self, ts, raw):
        if len(raw) < 15:
            return
        block = self._get_block_locked(raw[1])
        is_y = bool(raw[2])
        condition = {
            "is_y": is_y,
            "center": self._s16(raw, 3) / 10000.0,
            "pos_coeff": self._s16(raw, 5) / 10000.0,
            "neg_coeff": self._s16(raw, 7) / 10000.0,
            "pos_sat": self._u16(raw, 9) / 10000.0,
            "neg_sat": self._u16(raw, 11) / 10000.0,
            "deadband": self._u16(raw, 13) / 10000.0,
        }
        if is_y:
            block["condition_y"] = condition
            block["axis_y"] = True
            # Keep an X condition if one already arrived; otherwise this is
            # a Y-only effect and must not drive the wheel's X axis.
            if block.get("condition_x") is None:
                block["axis_x"] = False
                block["direction_x"] = 0.0
                block["direction_y"] = 1.0
                block["direction_deg"] = 90.0
        else:
            block["condition_x"] = condition
            block["condition"] = condition
            block["axis_x"] = True
            block["direction_x"] = 1.0
            block["direction_y"] = 0.0
            block["direction_deg"] = 0.0
        block["polar"] = False
        if block.get("started_at") is None:
            block["started_at"] = ts
        block["updated"] = ts

    def _parse_constant_locked(self, ts, raw):
        if len(raw) < 4:
            return
        block = self._get_block_locked(raw[1])
        magnitude = max(-10000, min(10000, self._s16(raw, 2))) / 10000.0
        block["constant"] = magnitude
        block["updated"] = ts
        if block.get("started_at") is None:
            block["started_at"] = ts
        self.raw_ffb_magnitude = magnitude

    def _parse_periodic_locked(self, ts, raw):
        if len(raw) < 12:
            return
        block = self._get_block_locked(raw[1])
        magnitude = self._u16(raw, 2) / 10000.0
        offset = max(-10000, min(10000, self._s16(raw, 4))) / 10000.0
        phase = self._u16(raw, 6)
        period = struct.unpack_from("<I", raw, 8)[0]
        old = block.get("periodic")
        block["periodic"] = {
            "magnitude": magnitude,
            "offset": offset,
            "phase": phase,
            "period": period,
        }
        if old is None:
            base = block.get("started_at")
            if base is not None:
                block["periodic_t0"] = base + block.get("start_delay_ms", 0) / 1000.0
            else:
                block["periodic_t0"] = ts
        elif old.get("phase") != phase or old.get("period") != period:
            block["periodic_t0"] = ts
        block["updated"] = ts
        if block.get("started_at") is None:
            block["started_at"] = ts

    def _parse_ramp_locked(self, ts, raw):
        if len(raw) < 6:
            return
        block = self._get_block_locked(raw[1])
        start = max(-10000, min(10000, self._s16(raw, 2))) / 10000.0
        end = max(-10000, min(10000, self._s16(raw, 4))) / 10000.0
        old = block.get("ramp")
        block["ramp"] = {"start": start, "end": end}
        if old is None:
            base = block.get("started_at")
            if base is not None:
                block["ramp_t0"] = base + block.get("start_delay_ms", 0) / 1000.0
            else:
                block["ramp_t0"] = ts
        block["updated"] = ts
        if block.get("started_at") is None:
            block["started_at"] = ts

    def _parse_operation_locked(self, ts, raw):
        if len(raw) < 3:
            return
        block = self._get_block_locked(raw[1])
        op = int(raw[2])
        if op in (1, 2):
            # Start is authoritative: the start delay and duration both begin
            # here, even if the Set Effect Report arrived slightly earlier.
            block["playing"] = True
            block["started_at"] = ts
            start_time = ts + max(0.0, block.get("start_delay_ms", 0) / 1000.0)
            block["periodic_t0"] = start_time
            block["ramp_t0"] = start_time
            if op == 2:
                for other in self._ffb_blocks.values():
                    if other is not block:
                        other["playing"] = False
        elif op == 3:
            block["playing"] = False
        block["updated"] = ts

    def _parse_control_locked(self, raw):
        if len(raw) < 2:
            return
        command = int(raw[1])
        if command == 1:
            self._ffb_actuators_enabled = True
        elif command == 2:
            self._ffb_actuators_enabled = False
        elif command == 3:
            for block in self._ffb_blocks.values():
                block["playing"] = False
        elif command == 4:
            self._ffb_blocks.clear()
            self._device_gain = 1.0
            self._ffb_actuators_enabled = True
            self._ffb_paused = False
        elif command == 5:
            self._ffb_paused = True
        elif command == 6:
            self._ffb_paused = False

    def _debug_ffb_packet(self, ts, ptype, raw):
        now = time.perf_counter()
        block = self._report_block_index(ptype, raw)
        key = (ptype, block)
        if key not in self._debug_seen and len(self._debug_seen) >= 64:
            return
        if now - self._debug_last_print < 0.25 and key in self._debug_seen:
            return
        self._debug_seen.add(key)
        self._debug_last_print = now
        hex_data = raw[:24].hex(" ")
        print(f"[FFB 0x{ptype:02X}] block={block} len={len(raw)} | {hex_data}")

    def _start_capture(self, seconds=10.0):
        if self._ffb_capture_until:
            return
        name = time.strftime("ffb_capture_%Y%m%d_%H%M%S.log")
        self._ffb_capture_path = os.path.join(APP_DIR, name)
        self._ffb_capture_until = time.time() + float(seconds)
        self._ffb_capture_count = 0
        self._capture_done_path = None
        self._capture_error = None

    def _update_capture(self):
        now = time.time()
        if not self._ffb_capture_until:
            return
        if self._ffb_capture_file is None:
            try:
                self._ffb_capture_file = open(self._ffb_capture_path, "w", encoding="utf-8", buffering=1)
                self._ffb_capture_file.write(
                    "# Virtual Wheel raw FFB capture\n"
                    "# Columns: perf_counter, report_type_hex, block, raw_data_hex\n"
                )
            except Exception as exc:
                self._capture_error = str(exc)
                self._ffb_capture_until = 0.0
                return
        if now >= self._ffb_capture_until:
            self._close_capture()
            self._capture_done_path = self._ffb_capture_path

    def _write_capture(self, ts, ptype, raw):
        if self._ffb_capture_file is None:
            return
        try:
            block = self._report_block_index(ptype, raw)
            block_text = "" if block is None else str(block)
            self._ffb_capture_file.write(f"{ts:.6f}\t0x{ptype:02X}\t{block_text}\t{raw.hex(' ')}\n")
            self._ffb_capture_count += 1
        except Exception as exc:
            self._capture_error = str(exc)
            self._close_capture()

    def _close_capture(self):
        if self._ffb_capture_file is not None:
            try:
                self._ffb_capture_file.close()
            except Exception:
                pass
            self._ffb_capture_file = None
        self._ffb_capture_until = 0.0

    def _apply_initial_window_geometry(self):
        """Place the window inside the usable desktop area, above the taskbar."""
        area = _get_work_area()
        self._last_work_area = area
        left, top, right, bottom = area
        work_w = max(640, right - left)
        work_h = max(480, bottom - top)
        margin = 14
        chrome_h = 48  # title bar + window borders
        width = min(960, work_w - 2 * margin)
        height = min(650, work_h - chrome_h - 2 * margin)
        width = max(width, min(880, work_w - 2 * margin))
        height = max(height, min(600, work_h - chrome_h - 2 * margin))
        width = min(width, work_w - 2 * margin)
        height = min(height, work_h - chrome_h - 2 * margin)
        x = left + max(0, (work_w - width) // 2)
        y = top + max(0, (work_h - height - chrome_h) // 2)
        self.root.geometry(f"{int(width)}x{int(height)}+{int(x)}+{int(y)}")
        self.root.minsize(min(880, int(width)), min(600, int(height)))

    def _check_work_area_geometry(self):
        """Clamp the window after an RDP reconnect or desktop resolution change."""
        area = _get_work_area()
        if area == self._last_work_area:
            return
        self._last_work_area = area
        left, top, right, bottom = area
        work_w = max(640, right - left)
        work_h = max(480, bottom - top)
        margin = 14
        chrome_h = 48
        cur_w = self.root.winfo_width()
        cur_h = self.root.winfo_height()
        if cur_w <= 1 or cur_h <= 1:
            return
        new_w = min(cur_w, work_w - 2 * margin)
        new_h = min(cur_h, work_h - chrome_h - 2 * margin)
        x = self.root.winfo_x()
        y = self.root.winfo_y()
        x = max(left, min(x, right - new_w))
        y = max(top, min(y, bottom - new_h - chrome_h))
        self.root.minsize(min(880, int(new_w)), min(600, int(new_h)))
        if (new_w, new_h) != (cur_w, cur_h):
            self.root.geometry(f"{int(new_w)}x{int(new_h)}+{int(x)}+{int(y)}")
        elif (x, y) != (self.root.winfo_x(), self.root.winfo_y()):
            self.root.geometry(f"+{int(x)}+{int(y)}")

    # ----------------- TRAY ICON & WINDOW VISIBILITY -----------------
    def _post_ui(self, callback):
        """Queue a callable for the Tk main loop (tray actions run off-thread)."""
        self._ui_queue.put(callback)

    def _drain_ui_queue(self):
        while True:
            try:
                callback = self._ui_queue.get_nowait()
            except queue.Empty:
                return
            try:
                callback()
            except Exception as exc:
                print(f"[Tray] Action failed: {exc}")

    def _apply_window_icon(self):
        """Use the same generated gate artwork for the title bar / taskbar."""
        try:
            from PIL import ImageTk
            self._window_icon_photo = ImageTk.PhotoImage(_draw_app_icon(64))
            self.root.iconphoto(True, self._window_icon_photo)
        except Exception as exc:
            print(f"[UI] Window icon unavailable: {exc}")

    def _init_tray(self):
        """Create the notification-area icon used for hide/restore and exit."""
        try:
            import pystray
        except Exception as exc:
            if not self._tray_warned:
                self._tray_warned = True
                print(f"[Tray] pystray unavailable, tray icon disabled: {exc}")
            return

        try:
            image = _draw_app_icon(64)
        except Exception as exc:
            print(f"[Tray] Icon drawing failed: {exc}")
            return

        menu = pystray.Menu(
            pystray.MenuItem(
                "Show Virtual Wheel",
                lambda icon, item: self._post_ui(self._show_window),
                default=True,
            ),
            pystray.MenuItem(
                "Hide to tray",
                lambda icon, item: self._post_ui(self._hide_to_tray),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Minimize to tray on minimize",
                lambda icon, item: self._post_ui(self._toggle_minimize_to_tray),
                checked=lambda item: bool(self._minimize_to_tray),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Exit",
                lambda icon, item: self._post_ui(self.on_close),
            ),
        )
        try:
            icon = pystray.Icon("virtual_wheel", image, "Virtual Wheel", menu)
        except Exception as exc:
            print(f"[Tray] Icon creation failed: {exc}")
            return
        self._tray_icon = icon
        self._tray_thread = threading.Thread(
            target=self._run_tray, args=(icon,), name="VirtualWheelTray", daemon=True)
        self._tray_thread.start()

    def _run_tray(self, icon):
        try:
            icon.run()
        except Exception as exc:
            print(f"[Tray] Icon thread stopped: {exc}")

    def _stop_tray(self):
        icon = self._tray_icon
        self._tray_icon = None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass

    def _toggle_minimize_to_tray(self):
        self._minimize_to_tray = not self._minimize_to_tray
        print(f"[Tray] Minimize to tray {'enabled' if self._minimize_to_tray else 'disabled'}")
        try:
            self._save_settings()
        except Exception:
            pass
        if self._tray_icon is not None:
            try:
                self._tray_icon.update_menu()
            except Exception:
                pass

    def _show_window(self):
        if self._closing:
            return
        try:
            self.root.deiconify()
            self.root.state("normal")
            self.root.lift()
            # Briefly force topmost so a game's exclusive window cannot keep
            # the restored window behind it.
            self.root.attributes("-topmost", True)
            self.root.after(250, self._release_topmost)
            self.root.focus_force()
        except Exception as exc:
            print(f"[Tray] Restore failed: {exc}")
        self._unfocused_since = None
        self._idle_mode = False
        self._invalidate_visual_caches()
        self.root.after_idle(self._redraw_now)

    def _release_topmost(self):
        try:
            self.root.attributes("-topmost", False)
        except Exception:
            pass

    def _hide_to_tray(self):
        if self._closing:
            return
        try:
            self.root.withdraw()
        except Exception as exc:
            print(f"[Tray] Hide failed: {exc}")

    def _on_root_map(self, event=None):
        """Repaint as soon as the window is mapped again.

        Tk canvases are not guaranteed to keep their backing store across a
        minimize/withdraw cycle, which showed up as a brief black window when
        restoring from the taskbar. Invalidating the draw caches and repainting
        in the Map handler removes that blank frame.
        """
        if self._closing:
            return
        if event is not None and getattr(event, "widget", None) is not self.root:
            return
        try:
            if self.root.state() != "normal":
                return
        except Exception:
            return
        self._invalidate_visual_caches()
        self.root.after_idle(self._redraw_now)

    def _redraw_now(self):
        if self._closing:
            return
        try:
            self._reflow_visuals()
        except Exception as exc:
            print(f"[UI] Immediate redraw failed: {exc}")

    def _invalidate_visual_caches(self):
        for name in ("throttle_bar", "brake_bar", "ffb_bar",
                     "heavy_rumble_bar", "light_rumble_bar"):
            bar = getattr(self, name, None)
            if bar is not None:
                bar._vw_bar_last = None
        self._gate_last = None
        self._curve_last = None
        self._wheel_last = None
        self._ctrl_last_key = None

    def _is_window_foreground(self):
        try:
            user32 = ctypes.windll.user32
            # HWND is pointer-sized. Without explicit signatures ctypes truncates
            # it to a 32-bit int, so GetAncestor() and GetForegroundWindow()
            # never compare equal on 64-bit Windows.
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            user32.GetAncestor.restype = wintypes.HWND

            hwnd = int(self.root.winfo_id())
            root_hwnd = int(user32.GetAncestor(hwnd, 2) or 0)
            if not root_hwnd:
                root_hwnd = hwnd
            foreground = int(user32.GetForegroundWindow() or 0)
            return bool(foreground) and foreground == root_hwnd
        except Exception:
            return True

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        main_container = ttk.Frame(self.root, padding=(8, 8, 8, 6))
        main_container.grid(row=0, column=0, sticky="nsew")
        main_container.columnconfigure(0, weight=0)
        main_container.columnconfigure(1, weight=1)
        main_container.rowconfigure(0, weight=1)

        left_col = ttk.Frame(main_container)
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        right_col = ttk.Frame(main_container)
        right_col.grid(row=0, column=1, sticky="nsew")
        right_col.columnconfigure(0, weight=1)
        right_col.rowconfigure(0, weight=1)

        # ================= LEFT COLUMN: CONTROLS & SETTINGS =================

        hw_frame = ttk.LabelFrame(left_col, text=" Mode & Input Hardware ", padding=6)
        hw_frame.pack(fill="x", pady=(0, 4))

        ttk.Label(hw_frame, text="Gamepad Slot:", style="Small.TLabel").grid(row=0, column=0, sticky="w")
        self.controller_combo = ttk.Combobox(hw_frame, values=["Auto-Detect", "Slot 0", "Slot 1", "Slot 2", "Slot 3"], state="readonly", width=19)
        self.controller_combo.current(0)
        self.controller_combo.grid(row=0, column=1, padx=(6, 0), sticky="ew")
        self.controller_combo.bind("<<ComboboxSelected>>", self._on_runtime_setting_change)

        ttk.Label(hw_frame, text="Steering Mode:", style="Small.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.mode_combo = ttk.Combobox(hw_frame, values=["Gate Arc (Polar) Mode", "Standard X-Axis Mode"], state="readonly", width=19)
        self.mode_combo.current(0)
        self.mode_combo.grid(row=1, column=1, padx=(6, 0), pady=(2, 0), sticky="ew")
        self.mode_combo.bind("<<ComboboxSelected>>", self._on_runtime_setting_change)

        ttk.Label(hw_frame, text="Polling Rate:", style="Small.TLabel").grid(row=2, column=0, sticky="w", pady=(2, 0))
        self.polling_combo = ttk.Combobox(hw_frame, values=["250 Hz (4 ms)", "500 Hz (2 ms)"], state="readonly", width=19)
        self.polling_combo.current(0)
        self.polling_combo.grid(row=2, column=1, padx=(6, 0), pady=(2, 0), sticky="ew")
        self.polling_combo.bind("<<ComboboxSelected>>", self._on_runtime_setting_change)

        ttk.Label(hw_frame, text="FFB Source:", style="Small.TLabel").grid(row=3, column=0, sticky="w", pady=(2, 0))
        self.ffb_source_combo = ttk.Combobox(hw_frame, values=[
            "DirectInput Universal",
            "DirectInput Rich",
            "DirectInput Cartesian",
            "DirectInput Polar",
            "Assetto Corsa Shared Memory"
        ], state="readonly", width=19)
        self.ffb_source_combo.current(0)
        self.ffb_source_combo.grid(row=3, column=1, padx=(6, 0), pady=(2, 0), sticky="ew")
        self.ffb_source_combo.bind("<<ComboboxSelected>>", self._on_ffb_source_change)

        pedal_invert_frame = ttk.Frame(hw_frame)
        pedal_invert_frame.grid(row=4, column=0, columnspan=2, sticky="w", pady=(3, 0))
        ttk.Checkbutton(pedal_invert_frame, text="Invert throttle", variable=self.invert_throttle_var, command=self._on_runtime_setting_change).pack(side="left", padx=(0, 8))
        ttk.Checkbutton(pedal_invert_frame, text="Invert brake", variable=self.invert_brake_var, command=self._on_runtime_setting_change).pack(side="left")

        ttk.Checkbutton(hw_frame, text="Right stick buttons (15-18)", variable=self.rs_buttons_var, command=self._on_runtime_setting_change).grid(row=5, column=0, columnspan=2, sticky="w", pady=(2, 0))
        ttk.Checkbutton(hw_frame, text="FFB console debug log", variable=self.ffb_debug_var, command=self._on_ffb_debug_toggle).grid(row=6, column=0, columnspan=2, sticky="w", pady=(1, 0))
        hw_frame.columnconfigure(1, weight=1)

        # GATE ARC CONFIG
        gate_frame = ttk.LabelFrame(left_col, text=" Gate & Steering Arc ", padding=6)
        gate_frame.pack(fill="x", pady=(0, 4))

        ttk.Label(gate_frame, text="Max gate arc:", style="Small.TLabel").grid(row=0, column=0, sticky="w")
        self.max_arc_var = tk.DoubleVar(value=140.0)
        self.max_arc_slider = ttk.Scale(gate_frame, from_=90.0, to=180.0, variable=self.max_arc_var, command=self._on_slider_change)
        self.max_arc_slider.grid(row=0, column=1, sticky="ew", padx=6)
        self.max_arc_label = ttk.Label(gate_frame, text="140\u00b0", style="Small.TLabel", width=4, anchor="e")
        self.max_arc_label.grid(row=0, column=2)

        ttk.Label(gate_frame, text="Anti-whip slice:", style="Small.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.whip_width_var = tk.DoubleVar(value=30.0)
        self.whip_width_slider = ttk.Scale(gate_frame, from_=0.0, to=180.0, variable=self.whip_width_var, command=self._on_slider_change)
        self.whip_width_slider.grid(row=1, column=1, sticky="ew", padx=6, pady=(2, 0))
        self.whip_width_label = ttk.Label(gate_frame, text="30\u00b0", style="Small.TLabel", width=4, anchor="e")
        self.whip_width_label.grid(row=1, column=2, pady=(2, 0))

        ttk.Label(gate_frame, text="Whip inner radius:", style="Small.TLabel").grid(row=2, column=0, sticky="w", pady=(2, 0))
        self.whip_radius_var = tk.DoubleVar(value=0.50)
        self.whip_radius_slider = ttk.Scale(gate_frame, from_=0.00, to=1.00, variable=self.whip_radius_var, command=self._on_slider_change)
        self.whip_radius_slider.grid(row=2, column=1, sticky="ew", padx=6, pady=(2, 0))
        self.whip_radius_label = ttk.Label(gate_frame, text="0.50", style="Small.TLabel", width=4, anchor="e")
        self.whip_radius_label.grid(row=2, column=2, pady=(2, 0))

        self.anti_whip_var = tk.BooleanVar(value=True)
        self.anti_whip_check = ttk.Checkbutton(gate_frame, text="Smart anti-whip clamp (no lock jump)", variable=self.anti_whip_var, command=self._on_slider_change)
        self.anti_whip_check.grid(row=3, column=0, columnspan=3, sticky="w", pady=(3, 0))

        self.radial_blend_var = tk.BooleanVar(value=False)
        self.radial_blend_check = ttk.Checkbutton(gate_frame, text="Radial blend (return-to-center)", variable=self.radial_blend_var, command=self._on_slider_change)
        self.radial_blend_check.grid(row=4, column=0, columnspan=3, sticky="w", pady=(1, 0))

        gate_frame.columnconfigure(1, weight=1)

        # STEERING FILTERS
        steer_frame = ttk.LabelFrame(left_col, text=" Steering Response Filters ", padding=6)
        steer_frame.pack(fill="x", pady=(0, 4))

        ttk.Label(steer_frame, text="Gamma curve:", style="Small.TLabel").grid(row=0, column=0, sticky="w")
        self.gamma_var = tk.DoubleVar(value=2.0)
        self.gamma_slider = ttk.Scale(steer_frame, from_=1.0, to=4.0, variable=self.gamma_var, command=self._on_slider_change)
        self.gamma_slider.grid(row=0, column=1, sticky="ew", padx=6)
        self.gamma_label = ttk.Label(steer_frame, text="2.00", style="Small.TLabel", width=4, anchor="e")
        self.gamma_label.grid(row=0, column=2)

        ttk.Label(steer_frame, text="Speed limit:", style="Small.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.speed_var = tk.DoubleVar(value=0.08)
        self.speed_slider = ttk.Scale(steer_frame, from_=0.01, to=0.30, variable=self.speed_var, command=self._on_slider_change)
        self.speed_slider.grid(row=1, column=1, sticky="ew", padx=6, pady=(2, 0))
        self.speed_label = ttk.Label(steer_frame, text="0.08", style="Small.TLabel", width=4, anchor="e")
        self.speed_label.grid(row=1, column=2, pady=(2, 0))

        ttk.Label(steer_frame, text="Center deadzone:", style="Small.TLabel").grid(row=2, column=0, sticky="w", pady=(2, 0))
        self.deadzone_var = tk.DoubleVar(value=0.05)
        self.deadzone_slider = ttk.Scale(steer_frame, from_=0.00, to=1.00, variable=self.deadzone_var, command=self._on_slider_change)
        self.deadzone_slider.grid(row=2, column=1, sticky="ew", padx=6, pady=(2, 0))
        self.deadzone_label = ttk.Label(steer_frame, text="0.05", style="Small.TLabel", width=4, anchor="e")
        self.deadzone_label.grid(row=2, column=2, pady=(2, 0))
        steer_frame.columnconfigure(1, weight=1)

        # DIRECT FFB TO RUMBLE CONFIG & MOTOR INTENSITY
        ffb_frame = ttk.LabelFrame(left_col, text=" FFB to Rumble Engine ", padding=6)
        ffb_frame.pack(fill="x", pady=(0, 0))

        ttk.Label(ffb_frame, text="Heavy motor ceiling:", style="Small.TLabel").grid(row=0, column=0, sticky="w")
        self.heavy_gain_var = tk.DoubleVar(value=1.0)
        self.heavy_gain_slider = ttk.Scale(ffb_frame, from_=0.0, to=1.0, variable=self.heavy_gain_var, command=self._on_slider_change)
        self.heavy_gain_slider.grid(row=0, column=1, sticky="ew", padx=6)
        self.heavy_gain_label = ttk.Label(ffb_frame, text="100%", style="Small.TLabel", width=4, anchor="e")
        self.heavy_gain_label.grid(row=0, column=2)

        ttk.Label(ffb_frame, text="Light motor ceiling:", style="Small.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.light_gain_var = tk.DoubleVar(value=1.0)
        self.light_gain_slider = ttk.Scale(ffb_frame, from_=0.0, to=1.0, variable=self.light_gain_var, command=self._on_slider_change)
        self.light_gain_slider.grid(row=1, column=1, sticky="ew", padx=6, pady=(2, 0))
        self.light_gain_label = ttk.Label(ffb_frame, text="100%", style="Small.TLabel", width=4, anchor="e")
        self.light_gain_label.grid(row=1, column=2, pady=(2, 0))

        test_btn_box = ttk.Frame(ffb_frame)
        test_btn_box.grid(row=2, column=0, columnspan=3, pady=(4, 0))
        ttk.Button(test_btn_box, text="Test Heavy", command=lambda: self.pulse_rumble(heavy=True, light=False)).pack(side="left", padx=(0, 4))
        ttk.Button(test_btn_box, text="Test Light", command=lambda: self.pulse_rumble(heavy=False, light=True)).pack(side="left", padx=(0, 4))
        self.capture_btn = ttk.Button(test_btn_box, text="Capture FFB 10s", command=self._start_ffb_capture)
        self.capture_btn.pack(side="left")
        ffb_frame.columnconfigure(1, weight=1)

        # ================= RIGHT COLUMN: VISUALIZERS =================

        canvas_row = ttk.Frame(right_col)
        canvas_row.grid(row=0, column=0, sticky="nsew", pady=(0, 4))
        canvas_row.columnconfigure(0, weight=1, uniform="vis")
        canvas_row.columnconfigure(1, weight=1, uniform="vis")
        canvas_row.rowconfigure(0, weight=1)

        gate_vis_box = ttk.LabelFrame(canvas_row, text=" Gate & Anti-Whip ", padding=4)
        gate_vis_box.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        gate_vis_box.columnconfigure(0, weight=1)
        gate_vis_box.rowconfigure(0, weight=1)
        self.gate_canvas = tk.Canvas(gate_vis_box, width=250, height=220, bg="#1b1b1b", highlightthickness=0, bd=0)
        self.gate_canvas.grid(row=0, column=0, sticky="nsew")
        self._init_gate_canvas()

        curve_vis_box = ttk.LabelFrame(canvas_row, text=" Steering Curve ", padding=4)
        curve_vis_box.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        curve_vis_box.columnconfigure(0, weight=1)
        curve_vis_box.rowconfigure(0, weight=1)
        self.curve_canvas = tk.Canvas(curve_vis_box, width=250, height=220, bg="#1b1b1b", highlightthickness=0, bd=0)
        self.curve_canvas.grid(row=0, column=0, sticky="nsew")
        self._init_curve_canvas()

        gauge_box = ttk.LabelFrame(right_col, text=" Steering Wheel Angle ", padding=(6, 2))
        gauge_box.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        self.wheel_canvas = tk.Canvas(gauge_box, width=400, height=44, bg="#1b1b1b", highlightthickness=0, bd=0)
        self.wheel_canvas.pack(fill="x")
        self._init_wheel_canvas()

        lower_row = ttk.Frame(right_col)
        lower_row.grid(row=2, column=0, sticky="nsew")
        lower_row.columnconfigure(0, weight=1, minsize=210)
        lower_row.columnconfigure(1, weight=1, minsize=320)
        lower_row.rowconfigure(0, weight=1)

        telemetry_box = ttk.LabelFrame(lower_row, text=" Telemetry & FFB ", padding=5)
        telemetry_box.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        telemetry_box.columnconfigure(1, weight=1)
        telemetry_box.rowconfigure(5, weight=1)

        self.throttle_bar = self._make_telemetry_row(telemetry_box, 0, "Throttle", "#00FF66")
        self.brake_bar = self._make_telemetry_row(telemetry_box, 1, "Brake", "#FF3333")
        self.ffb_bar = self._make_telemetry_row(telemetry_box, 2, "FFB", "#CC44FF", signed=True)
        self.heavy_rumble_bar = self._make_telemetry_row(telemetry_box, 3, "Heavy", "#FF9900")
        self.light_rumble_bar = self._make_telemetry_row(telemetry_box, 4, "Light", "#00CCFF")

        self.ffb_health_label = ttk.Label(
            telemetry_box,
            text="FFB idle",
            font=("Segoe UI", 7),
            foreground="#888888",
            wraplength=220,
            justify="left"
        )
        self.ffb_health_label.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(3, 0))
        telemetry_box.bind("<Configure>", self._on_telemetry_resize)

        controller_box = ttk.LabelFrame(lower_row, text=" Controller Input ", padding=4)
        controller_box.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        controller_box.columnconfigure(0, weight=1)
        controller_box.rowconfigure(0, weight=1)
        self.controller_canvas = tk.Canvas(controller_box, width=320, height=180, bg="#181818",
                                            highlightthickness=0, bd=0)
        self.controller_canvas.grid(row=0, column=0, sticky="nsew")
        self._init_controller_canvas()
        self.ctrl_legend_label = ttk.Label(
            controller_box,
            text="A:1  B:2  X:3  Y:4   LB:5  RB:6\nBk:7  St:8   LS:9  RS:10   DPad:11-14   RS dir:15-18",
            font=("Segoe UI", 6),
            foreground="#777777",
            justify="center"
        )
        self.ctrl_legend_label.grid(row=1, column=0, sticky="ew", pady=(2, 0))

        # Bottom bar is a root grid row, so it can never be pushed behind the taskbar.
        bottom_frame = ttk.Frame(self.root, padding=(8, 4))
        bottom_frame.grid(row=1, column=0, sticky="ew")
        bottom_frame.columnconfigure(0, weight=1)

        self.status_label = ttk.Label(
            bottom_frame,
            text=f"Status: {self._initial_status_text}",
            font=("Segoe UI", 9, "bold"),
            foreground=self._initial_status_color
        )
        self.status_label.grid(row=0, column=0, sticky="w")

        bottom_controls = ttk.Frame(bottom_frame)
        bottom_controls.grid(row=0, column=1, sticky="e")
        self.deconflict_label = ttk.Label(bottom_controls, text="", font=("Segoe UI", 7, "italic"), foreground="#FFCC00")
        self.deconflict_label.pack(side="left", padx=(0, 6))
        self.deconflict_check = ttk.Checkbutton(
            bottom_controls,
            text="Bind deconflict (1.5 s / 60 s)",
            variable=self.deconflict_mode_var,
            command=self._on_deconflict_toggle
        )
        self.deconflict_check.pack(side="left", padx=(0, 8))
        self.save_btn = ttk.Button(bottom_controls, text="Save Settings", command=self._save_settings)
        self.save_btn.pack(side="left", padx=(0, 4))
        self.toggle_btn = ttk.Button(bottom_controls, text="Start Virtual Wheel", command=self.toggle_service)
        self.toggle_btn.pack(side="left")

        self.draw_curve(0.0, 0.0)
        self.draw_gate_vis(0.0, 0.0)
        self.draw_wheel_gauge(0.0)
        self.draw_controller_vis({}, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    def _make_telemetry_row(self, parent, row, label, color, signed=False):
        ttk.Label(parent, text=label, style="Telemetry.TLabel").grid(
            row=row, column=0, sticky="w", padx=(0, 6), pady=1)
        canvas = tk.Canvas(parent, width=80, height=13, bg="#202020", highlightthickness=0, bd=0)
        canvas.grid(row=row, column=1, sticky="ew", pady=1)
        self._init_bar(canvas, color, signed=signed)
        return canvas

    def _on_telemetry_resize(self, event):
        width = max(140, int(event.width) - 18)
        if width == getattr(self, "_ffb_wrap", None):
            return
        self._ffb_wrap = width
        if hasattr(self, "ffb_health_label"):
            self.ffb_health_label.configure(wraplength=width)

    # ----------------- CONFIG PERSISTENCE (settings.ini) -----------------
    def _migrate_settings(self):
        try:
            if (not os.path.exists(SETTINGS_FILE) and LEGACY_SETTINGS_FILE
                    and os.path.abspath(LEGACY_SETTINGS_FILE) != os.path.abspath(SETTINGS_FILE)
                    and os.path.exists(LEGACY_SETTINGS_FILE)):
                shutil.copy2(LEGACY_SETTINGS_FILE, SETTINGS_FILE)
            # Once settings live beside the EXE, remove the old per-user copy
            # so there is only one settings.ini to maintain.
            if (getattr(sys, "frozen", False)
                    and os.path.abspath(SETTINGS_FILE) != os.path.abspath(LEGACY_APPDATA_SETTINGS_FILE)
                    and os.path.isfile(SETTINGS_FILE)
                    and os.path.isfile(LEGACY_APPDATA_SETTINGS_FILE)):
                os.remove(LEGACY_APPDATA_SETTINGS_FILE)
        except Exception as exc:
            print(f"[Settings] Migration failed: {exc}")

    def _save_settings(self):
        cfg = self._snapshot_runtime_config()
        config = configparser.ConfigParser()
        config["SETTINGS"] = {
            "settings_version": "1",
            "controller_slot": str(cfg["controller_slot"]),
            "steering_mode": str(cfg["mode"]),
            "polling_rate": str(cfg["polling_label"]),
            "ffb_source": str(cfg["ffb_source"]),
            "max_arc": f"{cfg['max_arc']:.8f}",
            "whip_width": f"{cfg['whip_width']:.8f}",
            "whip_radius": f"{cfg['whip_radius']:.8f}",
            "anti_whip_enabled": str(cfg["anti_whip"]),
            "radial_blend_enabled": str(cfg["radial_blend"]),
            "gamma": f"{cfg['gamma']:.8f}",
            "speed_limit": f"{cfg['speed_limit']:.8f}",
            "deadzone": f"{cfg['deadzone']:.8f}",
            "heavy_gain": f"{cfg['heavy_gain']:.8f}",
            "light_gain": f"{cfg['light_gain']:.8f}",
            "invert_throttle": str(cfg["invert_throttle"]),
            "invert_brake": str(cfg["invert_brake"]),
            "rs_buttons_enabled": str(cfg["rs_buttons"]),
            "ffb_debug_enabled": str(bool(self.ffb_debug_var.get())),
            "minimize_to_tray": str(bool(self._minimize_to_tray)),
        }
        temp_path = SETTINGS_FILE + ".tmp"
        try:
            os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
            with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
                config.write(handle)
            os.replace(temp_path, SETTINGS_FILE)
            self.status_label.config(text="Status: Settings saved", foreground="#00E5FF")
            if self.status_restore_timer:
                try:
                    self.root.after_cancel(self.status_restore_timer)
                except Exception:
                    pass
            self.status_restore_timer = self.root.after(3000, self._restore_status_label)
        except Exception as exc:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass
            self.status_label.config(text=f"Status: Settings save failed: {exc}", foreground="#ff6666")
            print(f"[Settings] Save failed: {exc}")

    def _restore_status_label(self):
        self.status_restore_timer = None
        if self.running:
            if self._ffb_start_ok:
                self.status_label.config(text="Status: Running (vJoy + FFB active)", foreground="#00ff66")
            else:
                self.status_label.config(text="Status: Running (FFB unavailable — check vJoyConf)", foreground="#ffcc00")
        elif self.vjoy_acquired:
            self.status_label.config(text=f"Status: {self._initial_status_text}", foreground=self._initial_status_color)
        else:
            self.status_label.config(text=f"Status: {self._initial_status_text}", foreground="#ff6666")

    def _load_settings(self):
        self._migrate_settings()
        if not os.path.exists(SETTINGS_FILE):
            return
        config = configparser.ConfigParser()
        try:
            config.read(SETTINGS_FILE, encoding="utf-8")
            if "SETTINGS" not in config:
                return
            sec = config["SETTINGS"]

            def set_combo(combo, key):
                value = sec.get(key, "").strip()
                if value in tuple(str(v) for v in combo.cget("values")):
                    combo.set(value)

            def get_float(key, default, low, high):
                try:
                    value = float(sec.get(key, default))
                except Exception:
                    value = float(default)
                return max(low, min(high, value))

            def get_bool(key, default):
                try:
                    return sec.getboolean(key, fallback=default)
                except Exception:
                    return default

            set_combo(self.controller_combo, "controller_slot")
            set_combo(self.mode_combo, "steering_mode")
            set_combo(self.polling_combo, "polling_rate")
            set_combo(self.ffb_source_combo, "ffb_source")

            self.max_arc_var.set(get_float("max_arc", 140.0, 90.0, 180.0))
            self.whip_width_var.set(get_float("whip_width", 30.0, 0.0, 180.0))
            self.whip_radius_var.set(get_float("whip_radius", 0.50, 0.0, 1.0))
            self.anti_whip_var.set(get_bool("anti_whip_enabled", True))
            self.radial_blend_var.set(get_bool("radial_blend_enabled", False))
            self.gamma_var.set(get_float("gamma", 2.0, 1.0, 4.0))
            self.speed_var.set(get_float("speed_limit", 0.08, 0.01, 0.30))
            self.deadzone_var.set(get_float("deadzone", 0.05, 0.0, 0.95))
            self.heavy_gain_var.set(get_float("heavy_gain", 1.0, 0.0, 1.0))
            self.light_gain_var.set(get_float("light_gain", 1.0, 0.0, 1.0))
            self.invert_throttle_var.set(get_bool("invert_throttle", False))
            self.invert_brake_var.set(get_bool("invert_brake", False))
            self.rs_buttons_var.set(get_bool("rs_buttons_enabled", True))
            self.ffb_debug_var.set(get_bool("ffb_debug_enabled", False))
            self._minimize_to_tray = get_bool("minimize_to_tray", False)

            self._on_slider_change()
            if self.ffb_debug_var.get():
                self._on_ffb_debug_toggle()
        except Exception as exc:
            print(f"[Settings] Load failed: {exc}")

    def _snapshot_runtime_config(self):
        """Copy Tk values into a plain dictionary the worker may read safely."""
        if not hasattr(self, "mode_combo"):
            return self._runtime_cfg
        try:
            def clamp(value, low, high):
                return max(low, min(high, float(value)))

            polling_label = self.polling_combo.get()
            polling_hz = 500 if "500" in polling_label else 250
            cfg = {
                "controller_slot": self.controller_combo.get(),
                "mode": self.mode_combo.get(),
                "polling_label": polling_label,
                "polling_hz": polling_hz,
                "interval": 0.002 if polling_hz == 500 else 0.004,
                "ffb_source": self.ffb_source_combo.get(),
                "max_arc": clamp(self.max_arc_var.get(), 90.0, 180.0),
                "whip_width": clamp(self.whip_width_var.get(), 0.0, 180.0),
                "whip_radius": clamp(self.whip_radius_var.get(), 0.0, 1.0),
                "anti_whip": bool(self.anti_whip_var.get()),
                "radial_blend": bool(self.radial_blend_var.get()),
                "gamma": clamp(self.gamma_var.get(), 1.0, 4.0),
                "speed_limit": clamp(self.speed_var.get(), 0.01, 0.30),
                "deadzone": clamp(self.deadzone_var.get(), 0.0, 0.95),
                "heavy_gain": clamp(self.heavy_gain_var.get(), 0.0, 1.0),
                "light_gain": clamp(self.light_gain_var.get(), 0.0, 1.0),
                "invert_throttle": bool(self.invert_throttle_var.get()),
                "invert_brake": bool(self.invert_brake_var.get()),
                "rs_buttons": bool(self.rs_buttons_var.get()),
                "deconflict": bool(self.deconflict_mode_var.get()),
            }
            self._runtime_cfg = cfg
            return cfg
        except Exception:
            return self._runtime_cfg

    def _on_runtime_setting_change(self, event=None):
        if event is not None and getattr(event, "widget", None) is self.mode_combo:
            self.whip_clamped_side = 0
            self._last_gate_deg = None
        self._snapshot_runtime_config()

    def _on_ffb_source_change(self, event=None):
        self._clear_ffb_queue()
        with self._ffb_lock:
            self._reset_ffb_state_locked()
        self._release_ac_shm()
        self._snapshot_runtime_config()
        self._update_ffb_source_label()
        print(f"[FFB] Switched FFB signal source to: {self.ffb_source_combo.get()}")

    def _update_ffb_source_label(self):
        # The source is visible in the FFB Source combobox; no duplicate label.
        self._last_ffb_label_source = self.ffb_source_combo.get()

    def _clear_ffb_queue(self):
        while True:
            try:
                self._ffb_queue.get_nowait()
            except queue.Empty:
                break

    def _reset_ffb_state_locked(self):
        self._ffb_blocks.clear()
        self.raw_ffb_magnitude = 0.0
        self.current_ffb_torque = 0.0
        self.ffb_direction_deg = 0.0
        self._device_gain = 1.0
        self._ffb_actuators_enabled = True
        self._ffb_paused = False
        self._ffb_last_block = None

    def _on_deconflict_toggle(self):
        if self.deconflict_mode_var.get():
            self.deconflict_expire_time = time.monotonic() + 60.0
            with self._deconflict_lock:
                self.deconflict_queue.clear()
            self._deconflict_auto_off = False
            self.deconflict_label.config(text="Active (60s remaining)")
            print("[Bind Deconflict] Activated (1.5s delay on vJoy outputs, auto-off in 60s)")
        else:
            self.deconflict_expire_time = 0.0
            with self._deconflict_lock:
                self.deconflict_queue.clear()
            self.deconflict_label.config(text="")
            print("[Bind Deconflict] Disabled")
        self._snapshot_runtime_config()

    # ----------------- DEBUG CONSOLE SUMMONING -----------------
    def _on_ffb_debug_toggle(self):
        self._debug_enabled = bool(self.ffb_debug_var.get())
        if self._debug_enabled:
            self._open_debug_console()
            print("[FFB Debug] Console logging enabled (rate-limited). Use Capture FFB for full raw data.")
        else:
            print("[FFB Debug] Console logging disabled.")
            self._close_debug_console()

    def _open_debug_console(self):
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            try:
                ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
            except Exception:
                pass
            return
        try:
            if not kernel32.AllocConsole():
                return
        except Exception:
            return
        self._console_allocated = True
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        self._console_streams = []
        try:
            out = open("CONOUT$", "w", buffering=1, encoding="utf-8", errors="replace")
            err = open("CONOUT$", "w", buffering=1, encoding="utf-8", errors="replace")
            self._console_streams = [out, err]
            sys.stdout = out
            sys.stderr = err
        except Exception:
            pass

    def _close_debug_console(self):
        self._debug_enabled = False
        for stream in self._console_streams:
            try:
                stream.close()
            except Exception:
                pass
        self._console_streams = []
        if self._console_allocated:
            sys.stdout = self._orig_stdout
            sys.stderr = self._orig_stderr
            try:
                kernel32.FreeConsole()
            except Exception:
                pass
            self._console_allocated = False

    # ----------------- RETAINED CANVAS INITIALIZERS -----------------
    def _schedule_reflow(self, _event=None):
        """Coalesce resize events so dragging the window does not redraw every pixel.

        This is a throttle rather than a pure debounce: the first reflow runs
        70 ms after a resize starts, then the next one is scheduled when more
        configure events arrive. A pure debounce leaves the canvas contents
        frozen until the drag stops, which can feel like lag.
        """
        if self._closing:
            return
        if self._reflow_job is not None:
            return
        self._reflow_job = self.root.after(70, self._reflow_visuals)

    def _reflow_visuals(self):
        self._reflow_job = None
        if self._closing:
            return

        try:
            size = (self.gate_canvas.winfo_width(), self.gate_canvas.winfo_height())
            if size != self._gate_size:
                self._gate_size = size
                self._layout_gate_canvas()
        except Exception:
            pass
        try:
            size = (self.curve_canvas.winfo_width(), self.curve_canvas.winfo_height())
            if size != self._curve_size:
                self._curve_size = size
                self._layout_curve_canvas()
        except Exception:
            pass
        try:
            size = (self.wheel_canvas.winfo_width(), self.wheel_canvas.winfo_height())
            if size != self._wheel_size:
                self._wheel_size = size
                self._layout_wheel_gauge()
        except Exception:
            pass
        try:
            size = (self.controller_canvas.winfo_width(), self.controller_canvas.winfo_height())
            if size != self._ctrl_size:
                self._ctrl_size = size
                self._layout_controller_canvas()
        except Exception:
            pass

        for bar in (self.throttle_bar, self.brake_bar, self.ffb_bar,
                    self.heavy_rumble_bar, self.light_rumble_bar):
            try:
                self._layout_bar(bar)
            except Exception:
                pass

        self.draw_bar(self.throttle_bar, self.telemetry["throttle"] * 100)
        self.draw_bar(self.brake_bar, self.telemetry["brake"] * 100)
        self.draw_signed_bar(self.ffb_bar, self.telemetry["ffb_torque"])
        self.draw_bar(self.heavy_rumble_bar, self.telemetry["heavy_rumble"] * 100)
        self.draw_bar(self.light_rumble_bar, self.telemetry["light_rumble"] * 100)
        self.draw_wheel_gauge(self.telemetry["steer"])
        self.draw_gate_vis(self.telemetry["raw_x"], self.telemetry["raw_y"])
        self.draw_curve(self.telemetry["unfiltered_steer"], self.telemetry["steer"])
        self.draw_controller_vis(*self._ctrl_last_state)

    def _init_bar(self, canvas, color, signed=False):
        canvas._vw_bar_color = color
        canvas._vw_bar_signed = bool(signed)
        canvas._vw_bar_size = None
        canvas._vw_fill_id = None
        canvas.bind("<Configure>", self._schedule_reflow)
        self._layout_bar(canvas)

    def _layout_bar(self, canvas):
        try:
            w = int(canvas.winfo_width())
            h = int(canvas.winfo_height())
        except Exception:
            return
        if w <= 1:
            w = 120
        if h <= 1:
            h = 13
        if (w, h) == canvas._vw_bar_size:
            return
        canvas._vw_bar_size = (w, h)
        canvas._vw_bar_last = None
        canvas.delete("all")
        canvas.create_rectangle(0, 0, w, h, fill="#242424", outline="#333333")
        center = (w - 1) / 2.0
        if canvas._vw_bar_signed:
            canvas._vw_fill_id = canvas.create_rectangle(
                center, 1, center, h - 1, fill=canvas._vw_bar_color, outline="")
        else:
            canvas._vw_fill_id = canvas.create_rectangle(
                0, 1, 0, h - 1, fill=canvas._vw_bar_color, outline="")
        for pct in (25, 50, 75):
            x = 0.5 + (w - 1) * pct / 100.0
            canvas.create_line(x, 1, x, h - 1, fill="#101010")
        if canvas._vw_bar_signed:
            canvas.create_line(w / 2.0, 0, w / 2.0, h, fill="#e0e0e0")
        canvas.create_line(0.5, 0, 0.5, h, fill="#4a4a4a")
        canvas.create_line(w - 0.5, 0, w - 0.5, h, fill="#4a4a4a")

    def draw_bar(self, canvas, val_pct):
        fill_id = getattr(canvas, "_vw_fill_id", None)
        if fill_id is None:
            return
        size = getattr(canvas, "_vw_bar_size", None)
        if not size:
            return
        w, h = size
        if w <= 1 or h <= 1:
            return
        val = max(0.0, min(100.0, float(val_pct)))
        key = (w, h, round(val, 2))
        if getattr(canvas, "_vw_bar_last", None) == key:
            return
        canvas._vw_bar_last = key
        fill_w = (w - 1) * val / 100.0
        canvas.coords(fill_id, 0, 1, fill_w, h - 1)

    def draw_signed_bar(self, canvas, value):
        """Center-zero FFB bar: negative torque fills left, positive fills right."""
        fill_id = getattr(canvas, "_vw_fill_id", None)
        if fill_id is None:
            return
        size = getattr(canvas, "_vw_bar_size", None)
        if not size:
            return
        w, h = size
        if w <= 1 or h <= 1:
            return
        center = (w - 1) / 2.0
        value = max(-1.0, min(1.0, float(value)))
        key = (w, h, round(value, 3))
        if getattr(canvas, "_vw_bar_last", None) == key:
            return
        canvas._vw_bar_last = key
        half = abs(value) * center
        if value >= 0.0:
            canvas.coords(fill_id, center, 1, center + half, h - 1)
            color = "#66CCFF"
        else:
            canvas.coords(fill_id, center - half, 1, center, h - 1)
            color = "#FF6688"
        canvas.itemconfigure(fill_id, fill=color)

    # ----------------- STEERING CURVE -----------------
    def _curve_metrics(self, force=False):
        cached = getattr(self, "_curve_metrics_cache", None)
        if cached is not None and not force:
            return cached
        try:
            w = max(80, int(self.curve_canvas.winfo_width()))
            h = max(80, int(self.curve_canvas.winfo_height()))
        except Exception:
            w, h = 250, 250
        if w <= 1:
            w = 250
        if h <= 1:
            h = 250
        r = max(20.0, min(w, h) / 2.0 - 17.0)
        metrics = (w, h, w / 2.0, h / 2.0, r)
        self._curve_metrics_cache = metrics
        return metrics

    def _init_curve_canvas(self):
        self.curve_canvas.bind("<Configure>", self._schedule_reflow)
        self._layout_curve_canvas()

    def _layout_curve_canvas(self):
        c = self.curve_canvas
        w, h, cx, cy, r = self._curve_metrics(force=True)
        self._curve_last = None
        c.delete("all")
        c.create_rectangle(0, 0, w, h, fill="#1b1b1b", outline="")
        c.create_rectangle(cx - r, cy - r, cx + r, cy + r, outline="#2c2c2c", fill="#1d1d1d")
        for frac in (-0.5, 0.5):
            x = cx + frac * r
            y = cy - frac * r
            c.create_line(x, cy - r, x, cy + r, fill="#282828")
            c.create_line(cx - r, y, cx + r, y, fill="#282828")
        c.create_line(cx - r, cy, cx + r, cy, fill="#3a3a3a")
        c.create_line(cx, cy - r, cx, cy + r, fill="#3a3a3a")
        c.create_line(cx - r, cy + r, cx + r, cy - r, fill="#303030", dash=(3, 4))
        c.create_text(cx - r + 1, cy + r + 7, text="-1", anchor="n", fill="#555555", font=("Segoe UI", 6))
        c.create_text(cx + r - 1, cy + r + 7, text="+1", anchor="n", fill="#555555", font=("Segoe UI", 6))
        c.create_text(cx - r - 6, cy + r - 1, text="-1", anchor="e", fill="#555555", font=("Segoe UI", 6))
        c.create_text(cx - r - 6, cy - r + 1, text="+1", anchor="e", fill="#555555", font=("Segoe UI", 6))
        self._curve_line_id = c.create_line(
            cx - r, cy, cx + r, cy, fill="#00FFCC", width=2, capstyle="round")
        self._curve_dot_id = c.create_oval(
            cx - 5, cy - 5, cx + 5, cy + 5, fill="#FFCC00", outline="#FFFFFF", width=1)
        self._rebuild_curve()

    def _rebuild_curve(self):
        if not hasattr(self, "_curve_line_id"):
            return
        _w, _h, cx, cy, r = self._curve_metrics()
        g = self.gamma_var.get()
        coords = []
        for i in range(-100, 101):
            x_n = i / 100.0
            y_n = math.copysign(math.pow(abs(x_n), g), x_n)
            coords.extend([cx + (x_n * r), cy - (y_n * r)])
        self.curve_canvas.coords(self._curve_line_id, *coords)

    def draw_curve(self, raw_val=0.0, out_val=0.0):
        if not hasattr(self, "_curve_dot_id"):
            return
        _w, _h, cx, cy, r = self._curve_metrics()
        raw_val = max(-1.0, min(1.0, float(raw_val)))
        out_val = max(-1.0, min(1.0, float(out_val)))
        key = (round(raw_val, 3), round(out_val, 3), cx, cy, r)
        if getattr(self, "_curve_last", None) == key:
            return
        self._curve_last = key
        dot_x = cx + (raw_val * r)
        dot_y = cy - (out_val * r)
        self.curve_canvas.coords(self._curve_dot_id, dot_x - 5, dot_y - 5, dot_x + 5, dot_y + 5)

    # ----------------- GATE VISUALIZER -----------------
    def _gate_metrics(self, force=False):
        cached = getattr(self, "_gate_metrics_cache", None)
        if cached is not None and not force:
            return cached
        try:
            w = max(80, int(self.gate_canvas.winfo_width()))
            h = max(80, int(self.gate_canvas.winfo_height()))
        except Exception:
            w, h = 250, 250
        if w <= 1:
            w = 250
        if h <= 1:
            h = 250
        metrics = (w, h, w / 2.0, h / 2.0, max(20.0, min(w, h) / 2.0 - 16.0))
        self._gate_metrics_cache = metrics
        return metrics

    def _init_gate_canvas(self):
        self.gate_canvas.bind("<Configure>", self._schedule_reflow)
        self._layout_gate_canvas()

    def _rebuild_gate_static(self):
        self._layout_gate_canvas()

    def _layout_gate_canvas(self):
        c = self.gate_canvas
        w, h, cx, cy, r = self._gate_metrics(force=True)
        self._gate_last = None
        c.delete("all")
        c.create_rectangle(0, 0, w, h, fill="#1b1b1b", outline="")
        c.create_oval(cx - r, cy - r, cx + r, cy + r,
                      fill="#1e1e1e", outline="#4a4a4a", width=1.5)
        c.create_line(cx - r, cy, cx + r, cy, fill="#323232")
        c.create_line(cx, cy - r, cx, cy + r, fill="#323232")
        for angle_deg in range(0, 360, 30):
            rad = math.radians(angle_deg)
            cos_a = math.cos(rad)
            sin_a = math.sin(rad)
            if angle_deg % 90 == 0:
                inner = r - 9.0
                color = "#555555"
            else:
                inner = r - 5.0
                color = "#3d3d3d"
            c.create_line(
                cx + inner * cos_a, cy - inner * sin_a,
                cx + r * cos_a, cy - r * sin_a,
                fill=color, width=1)

        max_arc = max(1.0, self.max_arc_var.get())
        # The gate arc is intentionally thicker than the anti-whip overlay so
        # the cyan range stays readable where the purple wedge crosses it.
        c.create_arc(cx - r, cy - r, cx + r, cy + r,
                     start=90.0, extent=max_arc, outline="#00E5FF", style="arc", width=5)
        c.create_arc(cx - r, cy - r, cx + r, cy + r,
                     start=90.0, extent=-max_arc, outline="#00E5FF", style="arc", width=5)
        c.create_line(cx, cy, cx, cy - r, fill="#00E5FF", width=1, dash=(2, 3))

        if self.anti_whip_var.get() and self.whip_width_var.get() > 0.0:
            whip_w = min(180.0, max(0.0, self.whip_width_var.get()))
            inner_r = r * max(0.0, min(1.0, self.whip_radius_var.get()))
            start_a = 270.0 - (whip_w / 2.0)
            c.create_arc(cx - r, cy - r, cx + r, cy + r,
                         start=start_a, extent=whip_w, outline="#FF00FF", style="arc", width=2)
            if inner_r > 1.0:
                c.create_arc(cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r,
                             start=start_a, extent=whip_w, outline="#FF00FF", style="arc", width=1)
            for angle_deg in (start_a, start_a + whip_w):
                rad = math.radians(angle_deg)
                cos_a = math.cos(rad)
                sin_a = math.sin(rad)
                c.create_line(
                    cx + inner_r * cos_a, cy - inner_r * sin_a,
                    cx + r * cos_a, cy - r * sin_a,
                    fill="#FF00FF", width=1)

        dz_r = r * max(0.0, min(1.0, self.deadzone_var.get()))
        if dz_r > 1.0:
            c.create_oval(cx - dz_r, cy - dz_r, cx + dz_r, cy + dz_r,
                          outline="#FF3333", width=1)
        self._gate_dot_id = c.create_oval(
            cx - 5, cy - 5, cx + 5, cy + 5, fill="#FFCC00", outline="#FFFFFF", width=1)
        self.draw_gate_vis(self.telemetry["raw_x"], self.telemetry["raw_y"])

    def draw_gate_vis(self, stick_x, stick_y):
        if not hasattr(self, "_gate_dot_id"):
            return
        _w, _h, cx, cy, r = self._gate_metrics()
        stick_x = max(-1.0, min(1.0, float(stick_x)))
        stick_y = max(-1.0, min(1.0, float(stick_y)))
        mag = math.hypot(stick_x, stick_y)
        if mag > 1.0:
            stick_x /= mag
            stick_y /= mag
        key = (round(stick_x, 3), round(stick_y, 3), cx, cy, r)
        if getattr(self, "_gate_last", None) == key:
            return
        self._gate_last = key
        dot_x = cx + (stick_x * r)
        dot_y = cy - (stick_y * r)
        self.gate_canvas.coords(self._gate_dot_id, dot_x - 5, dot_y - 5, dot_x + 5, dot_y + 5)

    # ----------------- STEERING ANGLE GAUGE -----------------
    def _wheel_metrics(self, force=False):
        cached = getattr(self, "_wheel_metrics_cache", None)
        if cached is not None and not force:
            return cached
        try:
            w = max(160, int(self.wheel_canvas.winfo_width()))
            h = max(36, int(self.wheel_canvas.winfo_height()))
        except Exception:
            w, h = 400, 44
        if w <= 1:
            w = 400
        if h <= 1:
            h = 44
        x0 = 28.0
        x1 = max(x0 + 40.0, w - 28.0)
        y = min(19.0, h - 22.0)
        metrics = (w, h, x0, x1, max(10.0, y))
        self._wheel_metrics_cache = metrics
        return metrics

    def _init_wheel_canvas(self):
        self.wheel_canvas.bind("<Configure>", self._schedule_reflow)
        self._layout_wheel_gauge()

    def _layout_wheel_gauge(self):
        c = self.wheel_canvas
        w, h, x0, x1, y = self._wheel_metrics(force=True)
        self._wheel_last = None
        c.delete("all")
        c.create_rectangle(0, 0, w, h, fill="#1b1b1b", outline="")
        c.create_rectangle(x0, y - 4, x1, y + 4, fill="#2a2a2a", outline="#3c3c3c")
        center = (x0 + x1) / 2.0
        self._wheel_fill_id = c.create_rectangle(
            center, y - 4, center, y + 4, fill="#008FA8", outline="")
        for pct in (0, 25, 50, 75, 100):
            x = x0 + (x1 - x0) * pct / 100.0
            if pct in (0, 50, 100):
                c.create_line(x, y - 9, x, y + 9, fill="#b0b0b0", width=1)
            else:
                c.create_line(x, y - 6, x, y + 6, fill="#555555", width=1)
        c.create_text(x0, y + 16, text="-100%", anchor="w", fill="#666666", font=("Segoe UI", 6))
        c.create_text(center, y + 16, text="0", anchor="center", fill="#888888", font=("Segoe UI", 6))
        c.create_text(x1, y + 16, text="+100%", anchor="e", fill="#666666", font=("Segoe UI", 6))
        self._wheel_dot_id = c.create_oval(
            center - 4, y - 4, center + 4, y + 4,
            fill="#FFFFFF", outline="#00E5FF", width=1)

    def draw_wheel_gauge(self, pos):
        if not hasattr(self, "_wheel_dot_id"):
            return
        _w, _h, x0, x1, y = self._wheel_metrics()
        center = (x0 + x1) / 2.0
        pos = max(-1.0, min(1.0, float(pos)))
        key = (round(pos, 3), x0, x1, y)
        if getattr(self, "_wheel_last", None) == key:
            return
        self._wheel_last = key
        indicator_x = center + (pos * (x1 - x0) / 2.0)
        if indicator_x >= center:
            x_fill0, x_fill1 = center, indicator_x
        else:
            x_fill0, x_fill1 = indicator_x, center
        self.wheel_canvas.coords(self._wheel_fill_id, x_fill0, y - 4, x_fill1, y + 4)
        self.wheel_canvas.coords(self._wheel_dot_id, indicator_x - 4, y - 4, indicator_x + 4, y + 4)

    # ----------------- CONTROLLER WIREFRAME -----------------
    def _init_controller_canvas(self):
        self.controller_canvas.bind("<Configure>", self._schedule_reflow)
        self._layout_controller_canvas()

    def _layout_controller_canvas(self):
        c = self.controller_canvas
        try:
            w = max(200, int(c.winfo_width()))
            h = max(130, int(c.winfo_height()))
        except Exception:
            w, h = 320, 180
        self._ctrl_last_key = None
        if w <= 1:
            w = 320
        if h <= 1:
            h = 180
        # Without the body outline the controls are the whole graphic, so the
        # base grid is sized to their bounding box instead of the old 320-wide
        # controller shell. x=45 is the left edge of the grid.
        s = max(0.35, min((w - 6) / 230.0, (h - 4) / 126.0))
        ox = (w - 230.0 * s) / 2.0
        oy = (h - 126.0 * s) / 2.0

        def X(x):
            return ox + (x - 45.0) * s

        def Y(y):
            return oy + y * s

        c.delete("all")
        c.create_rectangle(0, 0, w, h, fill="#181818", outline="")
        self._ctrl_geom = {
            "s": s,
            "lt_box": (X(70), Y(8), X(125), Y(16)),
            "rt_box": (X(195), Y(8), X(250), Y(16)),
            "ls_cx": X(78), "ls_cy": Y(62), "stick_range": 10.0 * s,
            "rs_cx": X(200), "rs_cy": Y(102),
            "stick_dot_r": max(3.5, 6.0 * s),
        }

        # Triggers (LT / RT) as compact meters above the shoulders.
        c.create_rectangle(X(70), Y(7), X(125), Y(17), fill="#141414", outline="#3a3a3a")
        self._ctrl_lt_fill = c.create_rectangle(
            X(70), Y(8), X(70), Y(16), fill="#00FF66", outline="")
        c.create_text(X(60), Y(12), text="LT", anchor="e", fill="#888888", font=("Segoe UI", 6, "bold"))
        c.create_rectangle(X(195), Y(7), X(250), Y(17), fill="#141414", outline="#3a3a3a")
        self._ctrl_rt_fill = c.create_rectangle(
            X(195), Y(8), X(195), Y(16), fill="#00FF66", outline="")
        c.create_text(X(260), Y(12), text="RT", anchor="w", fill="#888888", font=("Segoe UI", 6, "bold"))

        # Bumpers (LB / RB) sit on the shoulders like the reference pad.
        self._ctrl_lb = c.create_rectangle(X(70), Y(21), X(125), Y(34), fill="#2b2b2b", outline="#4a4a4a")
        self._ctrl_lb_txt = c.create_text(X(97.5), Y(27.5), text="LB", fill="#aaaaaa", font=("Segoe UI", 6, "bold"))
        self._ctrl_rb = c.create_rectangle(X(195), Y(21), X(250), Y(34), fill="#2b2b2b", outline="#4a4a4a")
        self._ctrl_rb_txt = c.create_text(X(222.5), Y(27.5), text="RB", fill="#aaaaaa", font=("Segoe UI", 6, "bold"))

        # No body outline: the controls float directly on the panel background.
        def centered_label(oval_id, text, fill, size=6):
            """Place a label on the pixel centre of its button, nudged down so
            uppercase glyphs sit optically centred instead of on the baseline."""
            box = c.bbox(oval_id)
            if box:
                tx = (box[0] + box[2]) / 2.0
                ty = (box[1] + box[3]) / 2.0
            else:
                tx, ty = 0.0, 0.0
            item = c.create_text(
                tx, ty + 0.5, text=text, anchor="center", fill=fill,
                font=("Segoe UI", size, "bold"))
            # Tk rounds a text item's bounding box independently of the oval's
            # pixel bounds. Nudge the label by the measured remainder so odd-
            # width glyphs such as B and X do not sit a pixel off-centre.
            tbox = c.bbox(item)
            if tbox:
                dx = tx - (tbox[0] + tbox[2]) / 2.0
                if abs(dx) > 0.01:
                    c.move(item, dx, 0)
            return item

        # Guide, Back (7), and Start (8) in a row across the top centre.
        c.create_oval(X(154), Y(53), X(166), Y(64), fill="#181818", outline="#444444")
        self._ctrl_back = c.create_oval(X(139), Y(53), X(151), Y(64),
                                        fill="#2b2b2b", outline="#4a4a4a")
        self._ctrl_back_txt = centered_label(self._ctrl_back, "7", "#aaaaaa", 5)
        self._ctrl_start = c.create_oval(X(169), Y(53), X(181), Y(64),
                                         fill="#2b2b2b", outline="#4a4a4a")
        self._ctrl_start_txt = centered_label(self._ctrl_start, "8", "#aaaaaa", 5)

        def stick_well(cx, cy):
            c.create_oval(cx - 17 * s, cy - 17 * s, cx + 17 * s, cy + 17 * s,
                          fill="#151515", outline="#3a3a3a", width=max(1.0, 1.3 * s))
            c.create_oval(cx - 12 * s, cy - 12 * s, cx + 12 * s, cy + 12 * s,
                          outline="#262626", width=1)
            c.create_line(cx - 14 * s, cy, cx + 14 * s, cy, fill="#252525")
            c.create_line(cx, cy - 14 * s, cx, cy + 14 * s, fill="#252525")

        # Left stick (9), upper left like the reference.
        ls_cx, ls_cy = X(78), Y(62)
        stick_well(ls_cx, ls_cy)
        self._ctrl_ls_dot = c.create_oval(
            ls_cx - self._ctrl_geom["stick_dot_r"], ls_cy - self._ctrl_geom["stick_dot_r"],
            ls_cx + self._ctrl_geom["stick_dot_r"], ls_cy + self._ctrl_geom["stick_dot_r"],
            fill="#555555", outline="#222222")

        # D-pad (11-14), lower left.
        dp_ids = {}
        for name, rect in (
            ("up", (124, 83, 136, 99)),
            ("down", (124, 99, 136, 115)),
            ("left", (114, 93, 130, 105)),
            ("right", (130, 93, 146, 105)),
        ):
            dp_ids[name] = c.create_rectangle(
                X(rect[0]), Y(rect[1]), X(rect[2]), Y(rect[3]),
                fill="#303030", outline="#4f4f4f")
        c.create_rectangle(X(124), Y(93), X(136), Y(105),
                           fill="#242424", outline="#3a3a3a")
        self._ctrl_dp = dp_ids

        # Right stick (10) and 4-way look pips (15-18), lower right.
        rs_cx, rs_cy = X(200), Y(102)
        stick_well(rs_cx, rs_cy)
        pip_r = max(1.5, 2.2 * s)
        pip_dist = 13.0 * s
        self._ctrl_look = {
            "left": c.create_oval(rs_cx - pip_dist - pip_r, rs_cy - pip_r, rs_cx - pip_dist + pip_r, rs_cy + pip_r, fill="#454545", outline=""),
            "right": c.create_oval(rs_cx + pip_dist - pip_r, rs_cy - pip_r, rs_cx + pip_dist + pip_r, rs_cy + pip_r, fill="#454545", outline=""),
            "up": c.create_oval(rs_cx - pip_r, rs_cy - pip_dist - pip_r, rs_cx + pip_r, rs_cy - pip_dist + pip_r, fill="#454545", outline=""),
            "down": c.create_oval(rs_cx - pip_r, rs_cy + pip_dist - pip_r, rs_cx + pip_r, rs_cy + pip_dist + pip_r, fill="#454545", outline=""),
        }
        self._ctrl_rs_dot = c.create_oval(
            rs_cx - self._ctrl_geom["stick_dot_r"], rs_cy - self._ctrl_geom["stick_dot_r"],
            rs_cx + self._ctrl_geom["stick_dot_r"], rs_cy + self._ctrl_geom["stick_dot_r"],
            fill="#555555", outline="#222222")

        # Face buttons (A:1, B:2, X:3, Y:4), upper right.
        face = {}
        for name, (fx, fy, color) in (
            ("Y", (235, 46, "#FFCC00")),
            ("A", (235, 80, "#00FF66")),
            ("X", (218, 63, "#3388FF")),
            ("B", (252, 63, "#FF3333")),
        ):
            oval_id = c.create_oval(
                X(fx) - 7 * s, Y(fy) - 7 * s, X(fx) + 7 * s, Y(fy) + 7 * s,
                fill="#2b2b2b", outline=color, width=max(1.0, 1.2 * s))
            face[name] = (
                oval_id,
                centered_label(oval_id, name, color, 6),
                color,
            )
        self._ctrl_face = face

        self.draw_controller_vis(*self._ctrl_last_state)

    def draw_controller_vis(self, buttons, lx, ly, rx, ry, lt, rt):
        self._ctrl_last_state = (buttons, lx, ly, rx, ry, lt, rt)
        geom = self._ctrl_geom
        if not geom or not hasattr(self, "_ctrl_ls_dot"):
            return
        c = self.controller_canvas
        look_enabled = bool(self.rs_buttons_var.get())

        # Skip the whole item update when nothing visible changed. Most frames
        # repeat the previous controller state, and each Tk itemconfigure is a
        # separate redraw, so this keeps an idle window cheap.
        button_key = tuple(sorted(
            (str(name), bool(value)) for name, value in (buttons or {}).items()))
        state_key = (
            button_key,
            round(float(lx), 2), round(float(ly), 2),
            round(float(rx), 2), round(float(ry), 2),
            round(float(lt), 2), round(float(rt), 2),
            look_enabled, geom.get("s"),
        )
        if getattr(self, "_ctrl_last_key", None) == state_key:
            return
        self._ctrl_last_key = state_key

        # Trigger meters.
        lx0, ly0, lx1, ly1 = geom["lt_box"]
        c.coords(self._ctrl_lt_fill, lx0, ly0, lx0 + (lx1 - lx0) * max(0.0, min(1.0, lt)), ly1)
        rx0, ry0, rx1, ry1 = geom["rt_box"]
        c.coords(self._ctrl_rt_fill, rx0, ry0, rx0 + (rx1 - rx0) * max(0.0, min(1.0, rt)), ry1)

        # Bumpers.
        for name, button_id, text_id, active in (
            ("LEFT_SHOULDER", self._ctrl_lb, self._ctrl_lb_txt, buttons.get("LEFT_SHOULDER", False)),
            ("RIGHT_SHOULDER", self._ctrl_rb, self._ctrl_rb_txt, buttons.get("RIGHT_SHOULDER", False)),
        ):
            c.itemconfigure(button_id, fill="#00E5FF" if active else "#2b2b2b")
            c.itemconfigure(text_id, fill="#101010" if active else "#aaaaaa")

        # Center buttons.
        for name, button_id, text_id in (
            ("BACK", self._ctrl_back, self._ctrl_back_txt),
            ("START", self._ctrl_start, self._ctrl_start_txt),
        ):
            active = buttons.get(name, False)
            c.itemconfigure(button_id, fill="#00E5FF" if active else "#2b2b2b")
            c.itemconfigure(text_id, fill="#101010" if active else "#aaaaaa")

        # Left stick.
        ls_x = geom["ls_cx"] + max(-1.0, min(1.0, lx)) * geom["stick_range"]
        ls_y = geom["ls_cy"] - max(-1.0, min(1.0, ly)) * geom["stick_range"]
        dot_r = geom["stick_dot_r"]
        c.coords(self._ctrl_ls_dot, ls_x - dot_r, ls_y - dot_r, ls_x + dot_r, ls_y + dot_r)
        ls_click = buttons.get("LEFT_THUMB", False)
        ls_fill = "#FFCC00" if ls_click else ("#00E5FF" if (abs(lx) > 0.08 or abs(ly) > 0.08) else "#555555")
        c.itemconfigure(self._ctrl_ls_dot, fill=ls_fill, outline="#FFFFFF" if ls_click else "#222222")

        # D-pad.
        dp_active = {
            "up": buttons.get("DPAD_UP", False),
            "down": buttons.get("DPAD_DOWN", False),
            "left": buttons.get("DPAD_LEFT", False),
            "right": buttons.get("DPAD_RIGHT", False),
        }
        for direction, active in dp_active.items():
            c.itemconfigure(self._ctrl_dp[direction], fill="#00E5FF" if active else "#2b2b2b")

        # Right stick and look pips.
        rs_x = geom["rs_cx"] + max(-1.0, min(1.0, rx)) * geom["stick_range"]
        rs_y = geom["rs_cy"] - max(-1.0, min(1.0, ry)) * geom["stick_range"]
        c.coords(self._ctrl_rs_dot, rs_x - dot_r, rs_y - dot_r, rs_x + dot_r, rs_y + dot_r)
        rs_click = buttons.get("RIGHT_THUMB", False)
        # The look pips are an output-only reminder. Keep them (and the orange
        # deflection colour) inactive when the right-stick buttons option is
        # off, otherwise they imply a mapping that is not being sent to vJoy.
        rs_fill = "#FFCC00" if rs_click else (
            "#FF9900" if (look_enabled and (abs(rx) > 0.50 or abs(ry) > 0.50))
            else ("#00E5FF" if (abs(rx) > 0.08 or abs(ry) > 0.08) else "#555555"))
        c.itemconfigure(self._ctrl_rs_dot, fill=rs_fill, outline="#FFFFFF" if rs_click else "#222222")
        for direction, active in (
            ("left", rx < -0.50),
            ("right", rx > 0.50),
            ("up", ry > 0.50),
            ("down", ry < -0.50),
        ):
            lit = look_enabled and active
            c.itemconfigure(self._ctrl_look[direction], fill="#FF9900" if lit else "#3a3a3a")

        # Face buttons.
        for name, (button_id, text_id, color) in self._ctrl_face.items():
            active = buttons.get(name, False)
            c.itemconfigure(button_id, fill=color if active else "#2b2b2b")
            c.itemconfigure(text_id, fill="#101010" if active else color)

    def _on_slider_change(self, _=None):
        self.gamma_label.config(text=f"{self.gamma_var.get():.2f}")
        self.speed_label.config(text=f"{self.speed_var.get():.2f}")
        self.deadzone_label.config(text=f"{self.deadzone_var.get():.2f}")
        self.max_arc_label.config(text=f"{int(self.max_arc_var.get())}°")
        self.whip_width_label.config(text=f"{int(self.whip_width_var.get())}°")
        self.whip_radius_label.config(text=f"{self.whip_radius_var.get():.2f}")
        self.heavy_gain_label.config(text=f"{int(self.heavy_gain_var.get() * 100)}%")
        self.light_gain_label.config(text=f"{int(self.light_gain_var.get() * 100)}%")
        if hasattr(self, "_curve_line_id"):
            self._rebuild_curve()
        if hasattr(self, "_gate_dot_id"):
            self._rebuild_gate_static()
            self.draw_gate_vis(self.telemetry["raw_x"], self.telemetry["raw_y"])
        self.draw_curve(self.telemetry["unfiltered_steer"], self.telemetry["steer"])
        self._snapshot_runtime_config()

    def pulse_rumble(self, heavy=True, light=True):
        self.test_heavy_val = self.heavy_gain_var.get() if heavy else 0.0
        self.test_light_val = self.light_gain_var.get() if light else 0.0
        self.test_rumble_until = time.perf_counter() + 0.4
        # While running, the process loop owns XInput output so it cannot be
        # overwritten by a competing test thread.
        if self.running and self._controller_connected:
            return
        def _test_thread():
            try:
                XInput.set_vibration(self.active_controller_index, self.test_heavy_val, self.test_light_val)
                time.sleep(0.35)
                XInput.set_vibration(self.active_controller_index, 0, 0)
            except Exception:
                pass
        threading.Thread(target=_test_thread, daemon=True).start()

    def _start_ffb_capture(self):
        if self._ffb_capture_until:
            self.status_label.config(text="Status: FFB capture already running", foreground="#ffcc00")
            return
        self._start_capture(10.0)
        self.capture_btn.config(state="disabled", text="Capturing 10s...")
        self.status_label.config(text="Status: Capturing raw FFB packets for 10 seconds...", foreground="#00E5FF")
        if self.status_restore_timer:
            try:
                self.root.after_cancel(self.status_restore_timer)
            except Exception:
                pass
        self.status_restore_timer = self.root.after(11000, self._restore_status_label)

    def _set_health_text(self, text):
        if getattr(self, "_health_label_text", None) == text:
            return
        self._health_label_text = text
        self.ffb_health_label.config(text=text)

    def _update_health_label(self):
        if not hasattr(self, "ffb_health_label"):
            return
        source = self.ffb_source_combo.get()
        if "Assetto Corsa" in source:
            with self._ac_lock:
                graphics_open = self.ac_graphics_shm is not None
                physics_open = self.ac_physics_shm is not None
            if physics_open:
                text = "AC1 shared memory open (read-only) · finalFF @ 308"
            elif graphics_open:
                text = "AC1 graphics open · waiting for physics"
            else:
                text = "AC1 waiting · use DirectInput Rich for ACC"
            self._set_health_text(text)
            return

        with self._ffb_lock:
            last = self._ffb_last_packet_time
            blocks = len(self._ffb_blocks)
            gain = self._device_gain
            last_type = self._ffb_last_type
            errors = self._ffb_errors
            drops = self._ffb_queue_drops
        now = time.perf_counter()
        if last:
            age = (now - last) * 1000.0
            age_text = f"{age:.0f} ms" if age < 1000.0 else f"{age / 1000.0:.1f} s"
            text = f"FFB {self._ffb_rate:.0f}/s · last {age_text} · {blocks} blk · {gain * 100:.0f}%"
        else:
            text = f"FFB idle · {blocks} blk · {gain * 100:.0f}%"
        if last_type:
            text += f" · 0x{last_type:02X}"
        if drops or errors:
            text += f" · drops {drops} err {errors}"
        self._set_health_text(text)

    def _start_gui_loop(self):
        if self._closing:
            return

        # Tray actions arrive from the pystray thread; run them on the Tk loop.
        self._drain_ui_queue()
        if self._closing:
            return

        # Pause rendering while minimized or hidden in the tray. When the
        # "minimize to tray" option is on, an iconic window is withdrawn so it
        # leaves the taskbar.
        try:
            state = self.root.state()
        except Exception:
            return
        if state in ("iconic", "withdrawn"):
            if state == "iconic" and self._minimize_to_tray:
                try:
                    self.root.withdraw()
                except Exception:
                    pass
            self._idle_mode = True
            self._unfocused_since = None
            self.root.after(200, self._start_gui_loop)
            return

        # Foreground pacing: 30 Hz while focused, 5 Hz once the window has been
        # in the background for 2 s. The slower rate still updates the visuals
        # but costs far less CPU while a game owns the foreground.
        now = time.perf_counter()
        if self._is_window_foreground():
            self._unfocused_since = None
            self._idle_mode = False
        else:
            if self._unfocused_since is None:
                self._unfocused_since = now
            self._idle_mode = (now - self._unfocused_since) >= 2.0
        delay = 200 if self._idle_mode else 33

        self._gui_tick_count += 1
        if self._gui_tick_last:
            self._gui_tick_gaps.append(now - self._gui_tick_last)
            if len(self._gui_tick_gaps) > 240:
                del self._gui_tick_gaps[:120]
        self._gui_tick_last = now

        # Deconflict countdown and auto-off.
        deconflict_text = ""
        if self.deconflict_mode_var.get():
            rem = int(self.deconflict_expire_time - time.monotonic())
            if rem > 0 and not self._deconflict_auto_off:
                deconflict_text = f"Active ({rem}s remaining)"
            else:
                self._deconflict_auto_off = True
                self.deconflict_mode_var.set(False)
                with self._deconflict_lock:
                    self.deconflict_queue.clear()
                self._snapshot_runtime_config()
                print("[Bind Deconflict] Auto-off timeout reached (disabled)")
        if getattr(self, "_deconflict_label_text", None) != deconflict_text:
            self._deconflict_label_text = deconflict_text
            self.deconflict_label.config(text=deconflict_text)

        self._update_ffb_source_label()

        self.draw_bar(self.throttle_bar, self.telemetry["throttle"] * 100)
        self.draw_bar(self.brake_bar, self.telemetry["brake"] * 100)
        self.draw_signed_bar(self.ffb_bar, self.telemetry["ffb_torque"])
        self.draw_bar(self.heavy_rumble_bar, self.telemetry["heavy_rumble"] * 100)
        self.draw_bar(self.light_rumble_bar, self.telemetry["light_rumble"] * 100)
        self.draw_wheel_gauge(self.telemetry["steer"])
        self.draw_gate_vis(self.telemetry["raw_x"], self.telemetry["raw_y"])
        self.draw_curve(self.telemetry["unfiltered_steer"], self.telemetry["steer"])

        inp = self.live_input_state
        self.draw_controller_vis(
            inp.get("buttons", {}),
            inp.get("lx", 0.0), inp.get("ly", 0.0),
            inp.get("rx", 0.0), inp.get("ry", 0.0),
            inp.get("lt", 0.0), inp.get("rt", 0.0)
        )

        now = time.perf_counter()
        if now - self._work_area_check >= 3.0:
            self._work_area_check = now
            self._check_work_area_geometry()
        if now - self._health_last_update >= 0.25:
            self._health_last_update = now
            self._update_health_label()

        if self._capture_error:
            error = self._capture_error
            self._capture_error = None
            self.capture_btn.config(state="normal", text="Capture FFB 10s")
            self.status_label.config(text=f"Status: FFB capture failed: {error}", foreground="#ff6666")
        elif self._capture_done_path:
            path = self._capture_done_path
            self._capture_done_path = None
            self.capture_btn.config(state="normal", text="Capture FFB 10s")
            self.status_label.config(
                text=f"Status: FFB capture saved: {os.path.basename(path)}",
                foreground="#00E5FF"
            )
            if self.status_restore_timer:
                try:
                    self.root.after_cancel(self.status_restore_timer)
                except Exception:
                    pass
            self.status_restore_timer = self.root.after(5000, self._restore_status_label)

        # Keep a steady UI rate even when a game owns the foreground; the idle
        # rate only kicks in after the window has been unfocused for a while.
        self.root.after(delay, self._start_gui_loop)

    # ----------------- SMART POLAR GATE & ANTI-WHIP MATH -----------------
    def _calculate_gate_steering(self, x, y, cfg):
        radius = math.hypot(x, y)
        dz = cfg["deadzone"]
        if radius < dz:
            self.whip_clamped_side = 0
            self._last_gate_deg = None
            return 0.0

        deg = math.degrees(math.atan2(y, x))
        if deg < 0:
            deg += 360.0

        # Top (12 o'clock, 90°) is center. Left half 90°->270° is negative;
        # right half 90°->0°->270° is positive.
        if 90.0 <= deg <= 270.0:
            offset_deg = -(deg - 90.0)
        elif deg < 90.0:
            offset_deg = 90.0 - deg
        else:
            offset_deg = 90.0 + (360.0 - deg)

        max_arc = max(1.0, cfg["max_arc"])
        clamped_norm = max(-1.0, min(1.0, offset_deg / max_arc))

        # Anti-whip: entering the bottom wedge from one side locks that side
        # only after the stick crosses 270°. The previous raw angle is used
        # (not current_wheel_pos) so the first wedge update cannot choose the
        # wrong side after a large speed-limited move.
        if cfg["anti_whip"]:
            whip_w = cfg["whip_width"]
            whip_radius = cfg["whip_radius"]
            if whip_w > 0.0:
                angle_min = 270.0 - (whip_w / 2.0)
                angle_max = 270.0 + (whip_w / 2.0)
                in_wedge = (radius >= whip_radius) and (angle_min <= deg <= angle_max)
                if in_wedge:
                    if self.whip_clamped_side == 0:
                        side = 0
                        if self._last_gate_deg is not None:
                            if self._last_gate_deg < 270.0 <= deg:
                                side = -1
                            elif self._last_gate_deg > 270.0 >= deg:
                                side = 1
                        if side == 0:
                            side = -1 if deg < 270.0 else 1
                        self.whip_clamped_side = side

                    if deg >= 270.0 and self.whip_clamped_side == -1:
                        clamped_norm = -1.0
                    elif deg < 270.0 and self.whip_clamped_side == 1:
                        clamped_norm = 1.0
                else:
                    self.whip_clamped_side = 0
            else:
                self.whip_clamped_side = 0
        else:
            self.whip_clamped_side = 0

        if cfg["radial_blend"]:
            safe_dz = min(dz, 0.80)
            r_scale = max(0.0, min(1.0, (radius - dz) / max(0.01, 0.85 - safe_dz)))
            clamped_norm *= r_scale

        self._last_gate_deg = deg
        return clamped_norm

    # ----------------- DIRECT FFB TO RUMBLE PROCESSING -----------------
    def _block_force_locked(self, block, now, wheel_pos, wheel_vel, wheel_acc):
        """Return one block's signed X-axis force in [-1, 1]."""
        if not self._ffb_actuators_enabled or self._ffb_paused:
            return 0.0
        if not block.get("playing", True):
            return 0.0

        started = block.get("started_at")
        duration = block.get("duration_ms", 0xFFFF)
        start_time = None
        if started is not None:
            start_time = started + max(0.0, block.get("start_delay_ms", 0) / 1000.0)
            if now < start_time:
                return 0.0
            if duration != 0xFFFF and (now - start_time) * 1000.0 > duration:
                return 0.0

        if not block.get("axis_x", True):
            return 0.0

        et = block.get("type", 0)
        condition = block.get("condition_x")
        periodic = block.get("periodic")
        ramp = block.get("ramp")
        force = 0.0

        if et == ET_CONST and block.get("constant") is not None:
            force = block["constant"]
        elif et in (ET_SPRNG, ET_DMPR, ET_INRT, ET_FRCTN) and condition:
            deadband = condition["deadband"]
            pos_coeff = condition["pos_coeff"]
            neg_coeff = condition["neg_coeff"]
            if et == ET_SPRNG:
                span = condition["center"] - wheel_pos
                if abs(span) <= deadband:
                    force = 0.0
                else:
                    coeff = pos_coeff if span >= 0.0 else neg_coeff
                    force = coeff * span
            elif et == ET_DMPR:
                # Damper coefficients follow velocity direction, not position.
                if abs(wheel_vel) <= deadband:
                    force = 0.0
                else:
                    coeff = pos_coeff if wheel_vel >= 0.0 else neg_coeff
                    force = -coeff * wheel_vel
            elif et == ET_INRT:
                # Inertia coefficients follow acceleration direction.
                if abs(wheel_acc) <= deadband:
                    force = 0.0
                else:
                    coeff = pos_coeff if wheel_acc >= 0.0 else neg_coeff
                    force = -coeff * wheel_acc
            else:
                # Friction opposes motion; coefficient follows velocity sign.
                if abs(wheel_vel) <= max(deadband, 1e-5):
                    force = 0.0
                else:
                    coeff = pos_coeff if wheel_vel >= 0.0 else neg_coeff
                    force = -coeff * math.copysign(1.0, wheel_vel)
            if force > 0.0 and condition["pos_sat"] > 0.0:
                force = min(force, condition["pos_sat"])
            elif force < 0.0 and condition["neg_sat"] > 0.0:
                force = max(force, -condition["neg_sat"])
        elif et in (ET_SQR, ET_SINE, ET_TRNGL, ET_STUP, ET_STDN) and periodic:
            period_ms = periodic["period"]
            phase = (periodic["phase"] / 100.0) / 360.0
            if period_ms > 0:
                phase_t0 = block.get("periodic_t0", now)
                if start_time is not None:
                    phase_t0 = max(phase_t0, start_time)
                phase += (now - phase_t0) / (period_ms / 1000.0)
            phase %= 1.0
            if et == ET_SQR:
                wave = 1.0 if math.sin(2.0 * math.pi * phase) >= 0.0 else -1.0
            elif et == ET_SINE:
                wave = math.sin(2.0 * math.pi * phase)
            elif et == ET_TRNGL:
                wave = 1.0 - 4.0 * abs(phase - 0.5)
            elif et == ET_STUP:
                wave = 2.0 * phase - 1.0
            else:
                wave = 1.0 - 2.0 * phase
            force = periodic["offset"] + periodic["magnitude"] * wave
        elif et == ET_RAMP and ramp:
            duration_ms = duration if duration != 0xFFFF else 1000.0
            alpha = 1.0
            if duration_ms > 0:
                ramp_t0 = block.get("ramp_t0", now)
                if start_time is not None:
                    ramp_t0 = max(ramp_t0, start_time)
                alpha = max(0.0, min(1.0, ((now - ramp_t0) * 1000.0) / duration_ms))
            force = ramp["start"] + (ramp["end"] - ramp["start"]) * alpha
        elif block.get("constant") is not None:
            force = block["constant"]
        elif condition:
            span = condition["center"] - wheel_pos
            coeff = condition["pos_coeff"] if span >= 0.0 else condition["neg_coeff"]
            if abs(span) > condition["deadband"]:
                force = coeff * span
        elif periodic:
            force = periodic["offset"]

        # Project the effect onto the wheel's X axis. Condition effects set
        # direction_x to 1.0 (X) or 0.0 (Y) from their condition report.
        force *= block.get("direction_x", 1.0)
        gain = max(0.0, min(1.0, block.get("gain", 1.0))) * self._device_gain
        return max(-1.0, min(1.0, force * gain))

    def _read_ac_ffb(self, now):
        """Read AC1 finalFF through read-only named mappings, never mmap-create."""
        with self._ac_lock:
            if now - self._ac_process_check >= 1.0:
                self._ac_process_check = now
                if not _is_process_running(("acs.exe", "assettocorsa.exe")):
                    self._release_ac_shm_locked()
                    return 0.0

            if self.ac_graphics_shm is None:
                if now - self.ac_shm_last_retry < 0.50:
                    return 0.0
                self.ac_shm_last_retry = now
                reader = SharedMemoryReader("Local\\acpmf_graphics", 64)
                if not reader.open():
                    return 0.0
                self.ac_graphics_shm = reader

            try:
                status = self.ac_graphics_shm.read(4, "<i")[0]
                if status != 2:  # AC_OFF/REPLAY/PAUSE; only live driving produces FFB
                    return 0.0
                if self.ac_physics_shm is None:
                    if now - self.ac_shm_last_retry < 0.25:
                        return 0.0
                    self.ac_shm_last_retry = now
                    physics = SharedMemoryReader("Local\\acpmf_physics", 712)
                    if not physics.open():
                        return 0.0
                    self.ac_physics_shm = physics
                raw_ff = self.ac_physics_shm.read(308, "<f")[0]
                return max(-1.0, min(1.0, float(raw_ff)))
            except Exception:
                self._release_ac_shm_locked()
                return 0.0

    def _release_ac_shm_locked(self):
        if self.ac_physics_shm is not None:
            try:
                self.ac_physics_shm.close()
            except Exception:
                pass
            self.ac_physics_shm = None
        if self.ac_graphics_shm is not None:
            try:
                self.ac_graphics_shm.close()
            except Exception:
                pass
            self.ac_graphics_shm = None

    def _release_ac_shm(self):
        with self._ac_lock:
            self._release_ac_shm_locked()

    def _compute_rumble_outputs(self, cfg, now, wheel_vel=0.0, wheel_acc=0.0):
        if now < self.test_rumble_until:
            return self.test_heavy_val, self.test_light_val

        source = cfg.get("ffb_source", "DirectInput Universal")
        if "Assetto Corsa" in source:
            self.current_ffb_torque = self._read_ac_ffb(now)
        else:
            with self._ffb_lock:
                blocks = list(self._ffb_blocks.values())
                all_sum = 0.0
                load_sum = 0.0
                texture_sum = 0.0
                has_load = False
                has_texture = False
                for block in blocks:
                    if not block.get("axis_x", True):
                        continue
                    force = self._block_force_locked(block, now, self.current_wheel_pos, wheel_vel, wheel_acc)
                    all_sum += force
                    if block.get("type") in (ET_CONST, ET_RAMP, ET_SPRNG, ET_DMPR, ET_INRT, ET_FRCTN):
                        load_sum += force
                        has_load = True
                    elif block.get("constant") is not None or block.get("condition_x") is not None:
                        load_sum += force
                        has_load = True
                    else:
                        texture_sum += force
                        has_texture = True
                # Legacy raw sources use the most recent active constant
                # block. Never fall back to a stopped/freed block or a stale
                # raw value after pause/reset.
                active_constants = [
                    b["constant"] for b in blocks
                    if b.get("playing", True) and b.get("constant") is not None
                ]
                raw = active_constants[-1] if active_constants else 0.0
                if self._ffb_paused or not self._ffb_actuators_enabled:
                    raw = 0.0
                if "DirectInput Rich" in source:
                    torque = all_sum if blocks else raw
                elif "DirectInput Cartesian" in source:
                    torque = raw
                elif "DirectInput Polar" in source:
                    sign = -1.0 if (180.0 <= self.ffb_direction_deg < 360.0) else 1.0
                    torque = sign * abs(raw)
                else:
                    torque = load_sum if has_load else (texture_sum if has_texture else raw)
                self.current_ffb_torque = max(-1.0, min(1.0, torque))

        torque_mag = abs(self.current_ffb_torque)
        heavy_output = min(1.0, torque_mag * cfg.get("heavy_gain", 1.0))
        light_output = min(1.0, torque_mag * cfg.get("light_gain", 1.0))
        return heavy_output, light_output

    # ----------------- VJOY OUTPUT & CONTROLLER HELPERS -----------------
    def _neutral_values(self):
        return (
            16384, 1, 1, 16384, 16384, 0xFFFFFFFF,
            {button_id: 0 for button_id in range(1, 33)}
        )

    def _apply_vjoy_outputs(self, v_x, v_y, v_z, v_rx, v_ry, pov_hw, btn_states, allow_closing=False):
        if not self.vjoy_acquired or (self._closing and not allow_closing):
            return

        button_mask = 0
        for button_id, state in btn_states.items():
            if state and 1 <= int(button_id) <= 32:
                button_mask |= (1 << (int(button_id) - 1))

        if self._vjoy_batch_supported and hasattr(vjoy, "UpdateVJD"):
            position = JOYSTICK_POSITION_V3()
            position.bDevice = self.vjoy_id
            position.wAxisX = int(v_x)
            position.wAxisY = int(v_y)
            position.wAxisZ = int(v_z)
            position.wAxisXRot = int(v_rx)
            position.wAxisYRot = int(v_ry)
            position.lButtons = ctypes.c_long(button_mask).value
            position.bHats = int(pov_hw) & 0xFFFFFFFF
            try:
                if vjoy.UpdateVJD(self.vjoy_id, ctypes.byref(position)):
                    return
            except Exception:
                pass
            # Fall back only once, then keep using the proven SetAxis/SetBtn path.
            self._vjoy_batch_supported = False
            print("[vJoy] UpdateVJD failed; using SetAxis/SetBtn fallback.")

        axes = (
            (int(v_x), HID_USAGE_X), (int(v_y), HID_USAGE_Y),
            (int(v_z), HID_USAGE_Z), (int(v_rx), HID_USAGE_RX),
            (int(v_ry), HID_USAGE_RY),
        )
        for value, usage in axes:
            if self._last_axis_values.get(usage) != value:
                try:
                    vjoy.SetAxis(value, self.vjoy_id, usage)
                    self._last_axis_values[usage] = value
                except Exception:
                    pass
        if self._last_pov_value != pov_hw:
            if self._vjoy_pov_count > 0:
                try:
                    vjoy.SetContPov(int(pov_hw), self.vjoy_id, 1)
                except Exception:
                    pass
            self._last_pov_value = pov_hw
        for button_id, state in btn_states.items():
            button_id = int(button_id)
            state = 1 if state else 0
            if self._last_btn_states.get(button_id) != state:
                try:
                    vjoy.SetBtn(state, self.vjoy_id, button_id)
                    self._last_btn_states[button_id] = state
                except Exception:
                    pass

    def _safe_zero_outputs(self):
        try:
            self._apply_vjoy_outputs(*self._neutral_values(), allow_closing=True)
        except Exception:
            pass
        self._last_rumble_values = (0.0, 0.0)
        self._last_rumble_time = 0.0
        try:
            XInput.set_vibration(self.active_controller_index, 0, 0)
        except Exception:
            pass

    def _send_rumble(self, heavy, light, now=None, force=False):
        if now is None:
            now = time.perf_counter()
        heavy = max(0.0, min(1.0, float(heavy)))
        light = max(0.0, min(1.0, float(light)))
        if not self._controller_connected and not force:
            if self._last_rumble_values != (0.0, 0.0):
                try:
                    XInput.set_vibration(self.active_controller_index, 0, 0)
                except Exception:
                    pass
                self._last_rumble_values = (0.0, 0.0)
            return
        if not force and now - self._last_rumble_time < 0.05:
            if abs(heavy - self._last_rumble_values[0]) < 0.005 and abs(light - self._last_rumble_values[1]) < 0.005:
                return
        try:
            XInput.set_vibration(self.active_controller_index, heavy, light)
            self._last_rumble_values = (heavy, light)
            self._last_rumble_time = now
        except Exception:
            pass

    def _get_controller_state(self, cfg, now):
        slot = cfg.get("controller_slot", "Auto-Detect")
        if slot != "Auto-Detect":
            try:
                index = int(slot.split("Slot ")[1][0])
            except Exception:
                index = 0
            self.active_controller_index = index
            try:
                return XInput.get_state(index)
            except Exception:
                return None

        # Auto-detect: first try the last known slot, then scan at a low rate.
        try:
            return XInput.get_state(self.active_controller_index)
        except Exception:
            pass
        if now - self._controller_scan_time < 0.75:
            return None
        self._controller_scan_time = now
        for index in range(4):
            try:
                state = XInput.get_state(index)
                self.active_controller_index = index
                return state
            except Exception:
                continue
        return None

    def _handle_controller_connected(self):
        if not self._controller_connected:
            self._controller_connected = True
            self._controller_lost_since = None
            self._controller_neutral_sent = False
            self._last_wheel_vel = 0.0

    def _handle_controller_lost(self, now):
        if self._controller_connected or self._controller_lost_since is None:
            self._controller_connected = False
            self._controller_lost_since = now
            self._controller_neutral_sent = False
            self._last_rumble_values = (0.0, 0.0)
            self._last_rumble_time = 0.0
            try:
                XInput.set_vibration(self.active_controller_index, 0, 0)
            except Exception:
                pass
            self.live_input_state = {
                "buttons": {},
                "lx": 0.0, "ly": 0.0,
                "rx": 0.0, "ry": 0.0,
                "lt": 0.0, "rt": 0.0
            }
        if not self._controller_neutral_sent and now - self._controller_lost_since >= 0.20:
            self.current_wheel_pos = 0.0
            self._last_wheel_vel = 0.0
            self._apply_vjoy_outputs(*self._neutral_values())
            self._controller_neutral_sent = True

    def _log_worker_error(self):
        self._worker_errors += 1
        now = time.perf_counter()
        if now - self._last_worker_error_time >= 1.0:
            self._last_worker_error_time = now
            print(f"[Process] Error:\n{traceback.format_exc()}")

    # ----------------- MAIN PROCESSING THREAD -----------------
    def toggle_service(self):
        if self._closing:
            return
        if not self.running:
            if self._worker_thread and self._worker_thread.is_alive():
                self.status_label.config(text="Status: Previous worker is still stopping — try again", foreground="#ffcc00")
                return
            if not self.vjoy_acquired:
                self._init_vjoy()
            if not self.vjoy_acquired:
                self.status_label.config(text=f"Status: {self._initial_status_text}", foreground="#ff6666")
                return

            if not self._ffb_started and hasattr(vjoy, "FfbStart"):
                try:
                    self._ffb_started = bool(vjoy.FfbStart(self.vjoy_id))
                except Exception:
                    self._ffb_started = False
            self._ffb_start_ok = self._ffb_started
            try:
                vjoy.FfbRegisterGenCB(self._ffb_c_cb, None)
            except Exception:
                pass

            self._clear_ffb_queue()
            with self._ffb_lock:
                self._reset_ffb_state_locked()
            self._snapshot_runtime_config()
            self._stop_event.clear()
            self._controller_connected = False
            self._controller_lost_since = None
            self._controller_neutral_sent = False
            self._last_tick_time = None
            self._last_axis_values.clear()
            self._last_pov_value = None
            self._last_btn_states.clear()
            self._last_rumble_values = (0.0, 0.0)
            self._last_rumble_time = 0.0
            self._deconflict_auto_off = False
            self.running = True
            self.toggle_btn.config(text="Stop Virtual Wheel")
            if self._ffb_start_ok:
                self.status_label.config(text="Status: Running (vJoy + FFB active)", foreground="#00ff66")
            else:
                self.status_label.config(text="Status: Running (FFB unavailable — check vJoyConf)", foreground="#ffcc00")
            self._worker_thread = threading.Thread(target=self._process_loop, name="input-worker", daemon=True)
            self._worker_thread.start()
        else:
            self._stop_event.set()
            self.running = False
            self.toggle_btn.config(text="Start Virtual Wheel")
            if self._worker_thread and self._worker_thread.is_alive():
                self._worker_thread.join(timeout=1.0)
            self._safe_zero_outputs()
            self._controller_connected = False
            with self._ffb_lock:
                self._reset_ffb_state_locked()
            self.status_label.config(text="Status: Stopped (vJoy held)", foreground="#ffffff")
            if self.status_restore_timer:
                try:
                    self.root.after_cancel(self.status_restore_timer)
                except Exception:
                    pass
                self.status_restore_timer = None

    def _process_loop(self):
        last_tick = time.perf_counter()
        self._last_tick_time = last_tick
        self._last_wheel_pos = self.current_wheel_pos
        self._last_wheel_vel = 0.0
        self._controller_connected = False
        self._controller_lost_since = None
        self._controller_neutral_sent = False

        while not self._stop_event.is_set() and not self._closing:
            cfg = self._runtime_cfg
            interval = float(cfg.get("interval", 0.004))
            next_tick = time.perf_counter() + interval
            try:
                now = time.perf_counter()
                dt = max(0.0005, min(0.05, now - last_tick))
                last_tick = now
                state = self._get_controller_state(cfg, now)

                if state is None:
                    self._handle_controller_lost(now)
                    heavy, light = self._compute_rumble_outputs(cfg, now, 0.0, 0.0)
                    self.telemetry["raw_x"] = 0.0
                    self.telemetry["raw_y"] = 0.0
                    self.telemetry["unfiltered_steer"] = 0.0
                    self.telemetry["steer"] = 0.0
                    self.telemetry["throttle"] = 0.0
                    self.telemetry["brake"] = 0.0
                    self.telemetry["ffb_torque"] = self.current_ffb_torque
                    self.telemetry["heavy_rumble"] = 0.0
                    self.telemetry["light_rumble"] = 0.0
                    sleep_for = next_tick - time.perf_counter()
                    if sleep_for > 0.0:
                        time.sleep(min(sleep_for, interval))
                    continue

                self._handle_controller_connected()

                raw_lx = state.Gamepad.sThumbLX / 32768.0
                raw_ly = state.Gamepad.sThumbLY / 32768.0
                raw_rx = state.Gamepad.sThumbRX / 32768.0
                raw_ry = state.Gamepad.sThumbRY / 32768.0
                lt = state.Gamepad.bLeftTrigger / 255.0
                rt = state.Gamepad.bRightTrigger / 255.0
                buttons = XInput.get_button_values(state)

                if "Gate Arc" in cfg["mode"]:
                    unfiltered_steer = self._calculate_gate_steering(raw_lx, raw_ly, cfg)
                else:
                    dz = min(cfg["deadzone"], 0.95)
                    if abs(raw_lx) <= dz:
                        unfiltered_steer = 0.0
                    else:
                        sign = math.copysign(1.0, raw_lx)
                        norm = (abs(raw_lx) - dz) / max(0.01, 1.0 - dz)
                        unfiltered_steer = sign * norm

                if unfiltered_steer != 0.0:
                    sign = math.copysign(1.0, unfiltered_steer)
                    target_pos = sign * math.pow(abs(unfiltered_steer), cfg["gamma"])
                else:
                    target_pos = 0.0

                # Preserve the existing tuning feel: the saved speed value was
                # authored against a 4 ms tick. Convert to units/second, then
                # scale by the actual dt so 250 Hz and 500 Hz behave identically.
                max_delta = min(1.0, (cfg["speed_limit"] / 0.004) * dt)
                previous_pos = self.current_wheel_pos
                diff = target_pos - self.current_wheel_pos
                if abs(diff) > max_delta:
                    self.current_wheel_pos += math.copysign(max_delta, diff)
                else:
                    self.current_wheel_pos = target_pos
                wheel_vel = (self.current_wheel_pos - previous_pos) / dt
                wheel_acc = (wheel_vel - self._last_wheel_vel) / dt
                self._last_wheel_vel = wheel_vel

                th_out = (1.0 - rt) if cfg["invert_throttle"] else rt
                br_out = (1.0 - lt) if cfg["invert_brake"] else lt
                v_x = max(1, min(32767, int(((self.current_wheel_pos + 1.0) / 2.0) * 32767)))
                v_y = max(1, min(32767, int(th_out * 32767)))
                v_z = max(1, min(32767, int(br_out * 32767)))
                v_rx = max(1, min(32767, int(((raw_rx + 1.0) / 2.0) * 32767)))
                v_ry = max(1, min(32767, int(((raw_ry + 1.0) / 2.0) * 32767)))

                pov_val = -1
                d_up = buttons.get("DPAD_UP", False)
                d_down = buttons.get("DPAD_DOWN", False)
                d_left = buttons.get("DPAD_LEFT", False)
                d_right = buttons.get("DPAD_RIGHT", False)
                if d_up and d_right:
                    pov_val = 4500
                elif d_right and d_down:
                    pov_val = 13500
                elif d_down and d_left:
                    pov_val = 22500
                elif d_left and d_up:
                    pov_val = 31500
                elif d_up:
                    pov_val = 0
                elif d_right:
                    pov_val = 9000
                elif d_down:
                    pov_val = 18000
                elif d_left:
                    pov_val = 27000
                pov_hw = pov_val if pov_val >= 0 else 0xFFFFFFFF

                btn_states = {
                    vjoy_btn_id: (1 if buttons.get(btn_name, False) else 0)
                    for btn_name, vjoy_btn_id in self.button_map.items()
                }
                if cfg["rs_buttons"]:
                    btn_states[15] = 1 if raw_rx < -0.50 else 0
                    btn_states[16] = 1 if raw_rx > 0.50 else 0
                    btn_states[17] = 1 if raw_ry > 0.50 else 0
                    btn_states[18] = 1 if raw_ry < -0.50 else 0
                else:
                    for button_id in (15, 16, 17, 18):
                        btn_states[button_id] = 0

                self.live_input_state = {
                    "buttons": buttons,
                    "lx": raw_lx, "ly": raw_ly,
                    "rx": raw_rx, "ry": raw_ry,
                    "lt": lt, "rt": rt
                }

                now_mono = time.monotonic()
                use_deconflict = bool(cfg.get("deconflict")) and not self._deconflict_auto_off
                if use_deconflict and now_mono > self.deconflict_expire_time:
                    self._deconflict_auto_off = True
                    use_deconflict = False
                    with self._deconflict_lock:
                        self.deconflict_queue.clear()

                if use_deconflict:
                    with self._deconflict_lock:
                        self.deconflict_queue.append(
                            (now_mono + 1.5, v_x, v_y, v_z, v_rx, v_ry, pov_hw, btn_states)
                        )
                        if self.deconflict_queue and self.deconflict_queue[0][0] <= now_mono:
                            _, out_x, out_y, out_z, out_rx, out_ry, out_pov, out_btns = self.deconflict_queue.pop(0)
                        else:
                            out_x, out_y, out_z, out_rx, out_ry, out_pov, out_btns = self._neutral_values()
                    self._apply_vjoy_outputs(out_x, out_y, out_z, out_rx, out_ry, out_pov, out_btns)
                else:
                    with self._deconflict_lock:
                        if self.deconflict_queue:
                            self.deconflict_queue.clear()
                    self._apply_vjoy_outputs(v_x, v_y, v_z, v_rx, v_ry, pov_hw, btn_states)

                heavy_rumble, light_rumble = self._compute_rumble_outputs(cfg, now, wheel_vel, wheel_acc)
                self._send_rumble(heavy_rumble, light_rumble, now)

                self.telemetry["raw_x"] = raw_lx
                self.telemetry["raw_y"] = raw_ly
                self.telemetry["unfiltered_steer"] = unfiltered_steer
                self.telemetry["steer"] = self.current_wheel_pos
                self.telemetry["throttle"] = th_out
                self.telemetry["brake"] = br_out
                self.telemetry["ffb_torque"] = self.current_ffb_torque
                self.telemetry["heavy_rumble"] = heavy_rumble
                self.telemetry["light_rumble"] = light_rumble
            except Exception:
                self._log_worker_error()

            sleep_for = next_tick - time.perf_counter()
            if sleep_for > 0.0:
                time.sleep(min(sleep_for, interval))
            else:
                next_tick = time.perf_counter()

        self._safe_zero_outputs()

    def on_close(self):
        if self._closing:
            return
        self._closing = True
        self.running = False
        self._stop_event.set()
        self._ffb_stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.5)
        if self._ffb_thread and self._ffb_thread.is_alive():
            self._ffb_thread.join(timeout=1.5)
        self._safe_zero_outputs()
        self._close_capture()
        self._release_ac_shm()
        if self.vjoy_acquired:
            try:
                if hasattr(vjoy, "FfbStop"):
                    vjoy.FfbStop(self.vjoy_id)
            except Exception:
                pass
            try:
                vjoy.RelinquishVJD(self.vjoy_id)
            except Exception:
                pass
            self.vjoy_acquired = False
        try:
            ctypes.windll.winmm.timeEndPeriod(1)
        except Exception:
            pass
        self._close_debug_console()
        self._stop_tray()
        try:
            self.root.destroy()
        except Exception:
            pass

if __name__ == "__main__":
    try:
        _enable_dpi_awareness()
        root = tk.Tk()
        app = UniversalWheelApp(root)
        root.mainloop()
    except Exception as exc:
        _fatal_dialog("Virtual Wheel failed to start", f"{exc}\n\n{traceback.format_exc()}")
        raise