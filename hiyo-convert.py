"""HiyoConvert - batch audio converter (multithreaded, ffmpeg backend).

Design notes:
  - One file on purpose. No modules, no registries. Menu and CLI share
    every function below the UI layer.
  - Originals are deleted ONLY after ffmpeg exits 0 and the output file
    exists with non-zero size. Anything else keeps the original.
  - Quality is one number 1-10, mapped per codec by quality_params().
"""

__version__ = "2.2.0"

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def _fix_stdout():
    """Force UTF-8 output. Guarded: pythonw.exe / IDLE have no .buffer."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        buf = getattr(stream, "buffer", None)
        enc = (getattr(stream, "encoding", "") or "").lower()
        if buf is not None and enc not in ("utf-8", "utf8"):
            try:
                setattr(sys, name, io.TextIOWrapper(buf, encoding="utf-8", errors="replace"))
            except Exception:
                pass


_fix_stdout()


L = None


class C:
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    RST = "\033[0m"
    RED = "\033[91m"
    GRN = "\033[92m"
    YLW = "\033[93m"
    BLU = "\033[94m"
    MAG = "\033[95m"
    CYN = "\033[96m"
    WHT = "\033[97m"
    LG = "\033[90m"


_lock = threading.Lock()


def cprint(text, color="", end="\n"):
    with _lock:
        sys.stdout.write(f"{color}{text}{C.RST}{end}" if color else f"{text}{end}")
        sys.stdout.flush()


def ask(prompt=""):
    """input() that survives EOF (piped stdin) and Ctrl+C."""
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return "\x03"


class Abort(Exception):
    pass


def clear_screen():
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def fade_out():
    for i in range(1, 4):
        cprint(f"\r  {C.DIM}{'─' * i}{C.RST}", "", "")
        time.sleep(0.05)
    cprint("")


def bounce_arrow(n=3):
    for _ in range(n):
        cprint(f"\r  {C.CYN}↳{C.RST}  ", "", "")
        time.sleep(0.08)
        cprint(f"\r  {C.CYN}  ↳{C.RST}", "", "")
        time.sleep(0.08)


def press_enter():
    cprint(f"\n  {C.ITALIC}{C.LG}{tr('press_enter')}{C.RST}", end="")
    ask()


def spinner_task(msg, task_fn, *args):
    """Run task_fn while showing a spinner. Rethrows the real exception."""
    done = threading.Event()
    box = {}

    def worker():
        try:
            box["r"] = task_fn(*args)
        except Exception as e:  # noqa: BLE001 - surfaced below
            box["e"] = e
        finally:
            done.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    frames = "|/-\\"
    i = 0
    cprint(f"  {msg} ", C.DIM, "")
    while not done.is_set():
        cprint(f"\r  {frames[i]} {msg} ", C.DIM, "")
        i = (i + 1) % 4
        time.sleep(0.08)
    if "e" in box:
        cprint(f"\r  {C.RED}✗{C.RST} {msg}  ")
        raise box["e"]
    cprint(f"\r  {C.GRN}✓{C.RST} {msg}  ")
    return box.get("r")


# --------------------------------------------------------------------------
# i18n
# --------------------------------------------------------------------------

LANG = {
    "en": {
        "title": "=== HiyoConvert ===",
        "lang": "Language / Ngôn ngữ:",
        "choose_lang": "Choose language / Chọn ngôn ngữ:",
        "quit": "Quit / Thoát",
        "all": "All / Tất cả",
        "ok": "OK",
        "skip": "SKIP",
        "fail": "FAIL",
        "done": "Done / Xong",
        "time": "Time",
        "threads": "Threads",
        "no_ffmpeg": "ffmpeg not found. Install: winget install Gyan.FFmpeg",
        "no_files": "No files found / Không tìm thấy file",
        "converting": "Converting",
        "files": "files",
        "scanning": "Scanning",
        "found": "Found",
        "lossless": "lossless",
        "original": "Original / Gốc",
        "none": "None / Không",
        "off": "OFF",
        "on": "ON",
        "yes": "YES",
        "no": "NO",
        "press_enter": "Press Enter / Nhấn Enter...",
        "back_to_menu": "Back to menu / Quay lại menu",
        "current_src": "Current source",
        "target": "Target",
        "pick_target": "Pick target format / Chọn định dạng đích",
        "change_dir": "Change source dir",
        "settings_hdr": "Settings",
        "quality": "Quality",
        "output_dir": "Output dir",
        "sample_rate": "Sample rate",
        "channels": "Channels",
        "normalize": "Normalize",
        "strip_meta": "Strip metadata",
        "simplify": "Simplify names",
        "strip_diacritics": "Strip diacritics",
        "keep": "Keep originals",
        "dry_run": "Dry-run",
        "start_now": "START",
        "choose_dir_title": "Select directory / Chọn thư mục",
        "choose_dir_curr": "Use current directory / Dùng thư mục hiện tại",
        "choose_dir_enter": "Enter path manually / Nhập đường dẫn thủ công",
        "choose_dir_browse": "Browse / Duyệt thư mục",
        "choose_dir_path": "Full path / Đường dẫn",
        "choose_dir_invalid": "Invalid path / Đường dẫn không hợp lệ",
        "choose_dir_pick": "Pick an option / Chọn một mục",
        "source_type": "Source type",
        "local_files": "Local files / File cục bộ",
        "youtube": "YouTube / Spotify URL",
        "enter_urls": "Enter URLs (comma or space separated, blank line to finish)",
        "no_urls": "No URLs entered / Chưa nhập URL",
        "invalid_url": "Invalid URL / URL không hợp lệ",
        "download_error": "Download failed / Tải thất bại",
        "confirm_title": "CONFIRM",
        "confirm_line": "converting",
        "confirm_warn_keep": "Originals are KEPT",
        "confirm_warn_delete": "ORIGINALS WILL BE DELETED after each successful convert",
        "confirm_run": "Enter to run / Esc to go back",
        "summary_title": "SUMMARY",
        "interrupted": "Interrupted. Waiting for current file to finish...",
        "cancelled": "cancelled",
        "eta": "left",
        "per_sec": "files/s",
        "processing": "Working on",
        "failed_list": "Failed files",
        "log_at": "Log",
        "of": "of",
        "to": "to",
        "size": "Size",
        "smaller": "smaller",
        "larger": "larger",
        "dry_run_title": "DRY-RUN",
        "would_convert": "Would convert",
        "src_local": "Local files",
        "src_url": "YouTube / Spotify URLs",
        "cfg_saved": "Settings saved",
        "err_ff": "ffmpeg failed",
        "no_output": "no output produced",
        "deleted": "originals deleted",
        "downloading": "Downloading",
        "playlist": "playlist",
        "src_filter": "Source filter",
        "language": "Language / Ngôn ngữ",
    },
    "vi": {
        "title": "=== HiyoConvert ===",
        "lang": "Ngôn ngữ / Language:",
        "choose_lang": "Chọn ngôn ngữ / Choose language:",
        "quit": "Thoát / Quit",
        "all": "Tất cả / All",
        "ok": "OK",
        "skip": "BỎ QUA",
        "fail": "LỖI",
        "done": "Xong / Done",
        "time": "Thời gian",
        "threads": "Luồng",
        "no_ffmpeg": "Chưa có ffmpeg. Cài: winget install Gyan.FFmpeg",
        "no_files": "Không tìm thấy file / No files found",
        "converting": "Đang chuyển đổi",
        "files": "tệp",
        "scanning": "Đang quét",
        "found": "Tìm thấy",
        "lossless": "không mất dữ liệu",
        "original": "Gốc / Original",
        "none": "Không / None",
        "off": "TẮT",
        "on": "BẬT",
        "yes": "CÓ",
        "no": "KHÔNG",
        "press_enter": "Nhấn Enter / Press Enter...",
        "back_to_menu": "Quay lại menu / Back to menu",
        "current_src": "Nguồn hiện tại",
        "target": "Định dạng đích",
        "pick_target": "Chọn định dạng đích / Pick target format",
        "change_dir": "Đổi thư mục nguồn",
        "settings_hdr": "Cài đặt",
        "quality": "Chất lượng",
        "output_dir": "Thư mục đích",
        "sample_rate": "Tần số lấy mẫu",
        "channels": "Kênh",
        "normalize": "Chuẩn hoá âm lượng",
        "strip_meta": "Xoá metadata",
        "simplify": "Rút gọn tên",
        "strip_diacritics": "Bỏ dấu tiếng Việt",
        "keep": "Giữ file gốc",
        "dry_run": "Chạy thử",
        "start_now": "BẮT ĐẦU",
        "choose_dir_title": "Chọn thư mục / Select directory",
        "choose_dir_curr": "Dùng thư mục hiện tại / Use current directory",
        "choose_dir_enter": "Nhập đường dẫn / Enter path manually",
        "choose_dir_browse": "Duyệt thư mục / Browse",
        "choose_dir_path": "Đường dẫn / Full path",
        "choose_dir_invalid": "Đường dẫn không hợp lệ / Invalid path",
        "choose_dir_pick": "Chọn một mục / Pick an option",
        "source_type": "Loại nguồn",
        "local_files": "File trên máy",
        "youtube": "URL YouTube / Spotify",
        "enter_urls": "Nhập URL (cách nhau bằng dấu phẩy hoặc khoảng trắng, dòng trống để kết thúc)",
        "no_urls": "Chưa nhập URL / No URLs entered",
        "invalid_url": "URL không hợp lệ / Invalid URL",
        "download_error": "Tải thất bại / Download failed",
        "confirm_title": "XÁC NHẬN",
        "confirm_line": "chuyển đổi",
        "confirm_warn_keep": "File gốc được GIỮ LẠI",
        "confirm_warn_delete": "FILE GỐC SẼ BỊ XOÁ sau mỗi lần chuyển đổi thành công",
        "confirm_run": "Enter để chạy / Esc để quay lại",
        "summary_title": "TÓM TẮT",
        "interrupted": "Đã dừng. Đang chờ file hiện tại hoàn tất...",
        "cancelled": "bị huỷ",
        "eta": "còn lại",
        "per_sec": "tệp/giây",
        "processing": "Đang xử lý",
        "failed_list": "File lỗi",
        "log_at": "Log",
        "of": "trên",
        "to": "thành",
        "size": "Dung lượng",
        "smaller": "nhỏ hơn",
        "larger": "lớn hơn",
        "dry_run_title": "CHẠY THỬ / DRY-RUN",
        "would_convert": "Sẽ chuyển đổi",
        "src_local": "File trên máy",
        "src_url": "URL YouTube / Spotify",
        "cfg_saved": "Đã lưu cài đặt",
        "err_ff": "ffmpeg lỗi",
        "no_output": "không tạo được file đích",
        "deleted": "đã xoá file gốc",
        "downloading": "Đang tải",
        "playlist": "danh sách",
        "src_filter": "Lọc nguồn",
        "language": "Ngôn ngữ / Language",
    },
}


def tr(key):
    """Translate. Missing key falls back to English, then to the key itself."""
    if L is None:
        return LANG["en"].get(key, key)
    val = L.get(key) or LANG["en"].get(key, key)
    return val.split(" / ", 1)[0] if " / " in val else val


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

CONFIG_DIR = (
    Path(os.environ["APPDATA"]) / "HiyoConvert"
    if sys.platform == "win32" and os.environ.get("APPDATA")
    else Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "hiyoconvert"
)
CONFIG_PATH = CONFIG_DIR / "config.json"


# --------------------------------------------------------------------------
# Codecs
# --------------------------------------------------------------------------

CODECS = {
    ".mp3":  {"name": "MP3 (LAME)",            "codec": "libmp3lame", "quality": ("inv_vbr", 0, 9),   "max_sr": 48000},
    ".ogg":  {"name": "OGG (Vorbis)",          "codec": "libvorbis",  "quality": ("vbr", 0, 10),     "max_sr": 96000},
    ".flac": {"name": "FLAC (Lossless)",       "codec": "flac",       "quality": ("level", 0, 8),    "max_sr": 192000},
    ".wav":  {"name": "WAV (PCM 16-bit)",      "codec": "pcm_s16le",  "quality": None,               "max_sr": 192000},
    ".m4a":  {"name": "AAC (M4A)",             "codec": "aac",        "quality": ("cbr", 96, 320),  "max_sr": 96000},
    ".opus": {"name": "Opus",                  "codec": "libopus",    "quality": ("cbr", 64, 320),  "max_sr": 48000},
    ".wma":  {"name": "WMA (Windows Media)",   "codec": "wmav2",      "quality": ("cbr", 64, 320),  "max_sr": 48000},
    ".ac3":  {"name": "AC3 (Dolby Digital)",   "codec": "ac3",        "quality": ("cbr", 64, 640),  "max_sr": 48000},
    # ALAC only exists inside an MP4 container. Writing to "*.alac" makes ffmpeg
    # fail with "Unable to choose an output format". The "ipod" muxer is
    # ffmpeg's name for .m4a; passing the .m4a extension as-is does not work.
    ".alac": {"name": "ALAC (Apple Lossless)", "codec": "alac",       "quality": None,               "max_sr": 96000, "container": "ipod"},
    # MP2 only accepts discrete bitrates; anything else fails with
    # "bitrate N is not allowed in mp2". quality_params snaps to this table.
    ".mp2":  {"name": "MP2 (MPEG Audio)",      "codec": "mp2",        "quality": ("cbr", 64, 384),  "max_sr": 48000,
              "allowed": [32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384]},
}

ALL_AUDIO_EXTS = {".mp3", ".ogg", ".flac", ".wav", ".m4a", ".opus",
                  ".wma", ".aiff", ".alac", ".ac3", ".dts", ".amr",
                  ".au", ".mp2", ".wv", ".tta", ".ape", ".spx"}

SRC_FORMATS = [
    ("All audio formats / Tất cả", None),
    ("MP3", ".mp3"), ("FLAC", ".flac"), ("OGG (Vorbis)", ".ogg"),
    ("WAV (PCM)", ".wav"), ("AAC (M4A)", ".m4a"), ("Opus", ".opus"),
    ("WMA", ".wma"), ("AIFF", ".aiff"), ("ALAC", ".alac"),
    ("AC3", ".ac3"), ("DTS", ".dts"), ("AMR", ".amr"), ("AU", ".au"),
    ("MP2", ".mp2"), ("WavPack", ".wv"), ("TTA", ".tta"),
    ("Monkey's Audio", ".ape"), ("Speex", ".spx"),
]

TGT_FORMATS = [(v["name"], k, k, v["codec"]) for k, v in CODECS.items()]

SAMPLE_RATES = [0, 44100, 48000, 96000, 192000]
CHANNELS = [0, 2, 1]


class Settings:
    FIELDS = ("quality", "output_dir", "sample_rate", "channels", "normalize",
              "strip_meta", "simplify", "keep", "dry_run", "strip_diacritics")
    EXTRA_FIELDS = ("force",)

    def __init__(self, **kw):
        self.quality = 5
        self.output_dir = None
        self.sample_rate = 0
        self.channels = 0
        self.normalize = False
        self.strip_meta = False
        self.simplify = False
        self.keep = False
        self.dry_run = False
        self.strip_diacritics = False
        self.force = False
        for k, v in kw.items():
            if k in self.FIELDS or k in self.EXTRA_FIELDS:
                setattr(self, k, v)

    def to_dict(self):
        d = {f: getattr(self, f) for f in self.FIELDS}
        d["output_dir"] = str(self.output_dir) if self.output_dir else None
        return d

    @classmethod
    def from_dict(cls, d):
        kw = dict(d)
        od = kw.get("output_dir")
        kw["output_dir"] = Path(od) if od else None
        return cls(**kw)

    def save(self, lang=None):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            payload = self.to_dict()
            if lang:
                payload["lang"] = lang
            tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, CONFIG_PATH)
            return True
        except OSError:
            return False

    @staticmethod
    def load():
        data = load_config()
        return Settings.from_dict(data) if data else None


def load_config():
    """Raw saved config, or an empty dict when missing/corrupt."""
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_saved_lang():
    lang = load_config().get("lang")
    return lang if lang in LANG else None


# --------------------------------------------------------------------------
# Pure helpers (unit-tested)
# --------------------------------------------------------------------------

def quality_params(codec_info, level):
    """Map 1-10 quality to ffmpeg args. Returns (args, human_label)."""
    q = codec_info.get("quality")
    if q is None:
        return [], tr("lossless")
    mode, lo, hi = q
    if mode == "inv_vbr":
        v = int(round(lo + (hi - lo) * (10 - level) / 9))
        return ["-q:a", str(v)], f"VBR q={v}"
    frac = (level - 1) / 9
    if mode == "vbr":
        v = int(round(lo + (hi - lo) * frac))
        return ["-q:a", str(v)], f"VBR q={v}"
    if mode == "cbr":
        v = max(lo, min(hi, int(round(lo + (hi - lo) * frac))))
        allowed = codec_info.get("allowed")
        if allowed:
            v = min(allowed, key=lambda a: (abs(a - v), a))
        return ["-b:a", f"{v}k"], f"{v}kbps"
    if mode == "level":
        v = int(round(lo + (hi - lo) * frac))
        return ["-compression_level", str(v)], f"level {v}"
    return [], ""


def strip_diacritics(text):
    # Đ/đ are single codepoints, not base+combining, so NFKD leaves them alone.
    text = text.replace("Đ", "D").replace("đ", "d")
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


_BRACKET_NOISE = re.compile(
    r'[\(\[][^\)\]]*'
    r'(?:official\s+(?:music\s+)?video|official\s+lyric\s+video|'
    r'lyric\s+video|lyrics\s+video|official\s+audio|'
    r'official\s+visualizer|lyric\s+visualizer|'
    r'lyrics|lyric|official|'
    r'remaster(?:ed)?|remix|rmx|edit|radio\s+edit|extended|'
    r'instrumental|acapella|demo|live|version|'
    r'explicit|clean|hd|hq|4k|kbps|'
    r'youtube|vevo|topic)'
    r'[^\)\]]*[\)\]]', re.IGNORECASE
)

_TRAIL_NOISE = re.compile(
    r'\s*[-–—]?\s*\b(?:official\s+(?:music\s+)?video|official\s+audio|'
    r'lyric\s+video|lyrics|lyric|official|hd|hq|4k|'
    r'remaster(?:ed)?|remix|edit|live|instrumental|audio|topic|'
    r'youtube|vevo)\b\s*$', re.IGNORECASE
)

_JUNK_CHARS = re.compile(
    r'[\u3000-\u30ff\u4e00-\u9fff\uff00-\uffef'
    r'\u2500-\u257f\u2580-\u259f\u25a0-\u25ff\u2600-\u26ff'
    r'\u2700-\u27bf\u2b00-\u2bff\u2000-\u206f'
    r'\u2100-\u214f\u2190-\u21ff\u2300-\u23ff'
    r'\u2400-\u243f\u2440-\u245f\u2460-\u24ff'
    r'\u2e80-\u2eff\u3000-\u303f\u3200-\u33ff'
    r'\ufe30-\ufe4f\ufe50-\ufe6f'
    r'\U0001F300-\U0001F9FF\U0001FA00-\U0001FA6F]'
)


def simplify_name(name):
    name = unicodedata.normalize("NFKC", name)
    name = _BRACKET_NOISE.sub("", name)
    prev = None
    while prev != name:
        prev = name
        name = _TRAIL_NOISE.sub("", name).strip()
    name = re.sub(r'\s*\[[\w-]{11}\]\s*', '', name)
    name = re.sub(r'\s*[-–—]\s*YouTube\s*$', '', name, flags=re.IGNORECASE)
    name = re.sub(
        r'\s*[-–—]?\s*[\(\[\{]?\s*(?:feat\.?|ft\.?|featuring|f\.)\s*'
        r'[^\]\)\}]*[\]\)\}]?\s*', ' ', name, flags=re.IGNORECASE
    )
    idx = name.find(" - ")
    if idx > 0:
        name = name[:idx]
    name = _JUNK_CHARS.sub("", name)
    name = re.sub(r'[\[\](){}#&,;:!?@$%^*+=<>"\'/\\|~`]', '', name)
    name = re.sub(r'[–—]', '-', name)
    name = re.sub(r'\s+', ' ', name).strip().strip('. ')
    if len(name) > 60:
        name = name[:60]
        cut = name.rfind(' ')
        if cut > 30:
            name = name[:cut]
    return name.strip() or "untitled"


# Windows refuses CON, PRN, AUX, NUL and COM1-9 / LPT1-9 as filenames, with any
# extension. Rename rather than fail the whole batch on one bad track name.
_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} \
            | {f"lpt{i}" for i in range(1, 10)}


def safe_stem(name):
    stem = name.rstrip(". ")
    if stem.lower() in _RESERVED or not stem:
        return f"_{stem}" if stem else "_"
    return stem


_INVALID_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')


def safe_filename(name, max_len=120):
    """Make a remote title safe as a single path component.

    Download titles are untrusted: they can contain '/', ':', '?' or be
    longer than the filesystem limit. Keep the readable text, replace
    forbidden characters, and never return an empty or reserved name.
    """
    name = unicodedata.normalize("NFKC", str(name or ""))
    name = _INVALID_FS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip().strip(". ")
    if len(name) > max_len:
        name = name[:max_len].rstrip(". ")
    return safe_stem(name) if name else "untitled"


def unique_path(parent, stem, ext, reserved):
    """Collision-free destination, also reserving names planned this run."""
    path = parent / f"{stem}{ext}"
    n = 1
    key = str(path).lower()
    while path.exists() or key in reserved:
        path = parent / f"{stem}_{n}{ext}"
        key = str(path).lower()
        n += 1
    reserved.add(key)
    return path


def output_parent(src, settings, root=None):
    """Where the converted file goes. Preserves the source's subfolder."""
    if not settings.output_dir:
        return src.parent
    if root:
        try:
            return settings.output_dir / src.relative_to(root).parent
        except ValueError:
            pass
    return settings.output_dir


def get_output_path(src, dst_ext, settings, root=None, create=True, reserved=None):
    name = src.stem
    if settings.simplify:
        name = simplify_name(name)
    if settings.strip_diacritics:
        name = strip_diacritics(name)
    name = safe_stem(name)
    parent = output_parent(src, settings, root)
    if create:
        parent.mkdir(parents=True, exist_ok=True)
    if reserved is None:
        reserved = set()
    return unique_path(parent, name, dst_ext, reserved)


def plan_jobs(files, dst_ext, settings, root=None):
    """Build (src, dst, display) jobs without racing on duplicate names.

    Collisions are reserved in memory. This matters when simplify or
    strip-diacritics maps two different source names onto one destination:
    checking only dst.exists() would hand both jobs the same path.
    Dry-run must not create directories, so create is disabled there.
    """
    reserved = set()
    jobs = []
    for f in files:
        dst = get_output_path(f, dst_ext, settings, root,
                              create=not settings.dry_run, reserved=reserved)
        jobs.append((f, dst, get_short_name(f.stem)))
    return jobs


def plan_download_jobs(got, tgt_ext, settings):
    """Turn downloaded (path, title) pairs into safe, collision-free jobs."""
    parent = settings.output_dir or Path.cwd()
    parent.mkdir(parents=True, exist_ok=True)
    reserved = set()
    jobs = []
    for path, title in got:
        dst = unique_path(parent, safe_filename(title), tgt_ext, reserved)
        jobs.append((path, dst, title))
    return jobs


def get_short_name(name):
    idx = name.find(" - ")
    return name[:idx] if idx > 0 else name


def format_size(n):
    if n < 1024:
        return f"{n}B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f}KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f}MB"
    return f"{n / 1024 ** 3:.2f}GB"


def fmt_dur(sec):
    sec = int(max(0, sec))
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m{sec % 60:02d}s"
    return f"{sec // 3600}h{(sec % 3600) // 60:02d}m"


def _match(f, src_ext):
    if not f.is_file():
        return False
    return f.suffix.lower() in (ALL_AUDIO_EXTS if src_ext is None else {src_ext})


def _first_match(d, src_ext, recursive):
    it = d.rglob("*") if recursive else d.iterdir()
    return next((f for f in it if _match(f, src_ext)), None)


def scan_dirs(root, src_ext):
    """Directories under root that hold audio. root included if it has audio."""
    dirs = []
    if _first_match(root, src_ext, recursive=False) is not None:
        dirs.append(root)
    for d in sorted(root.iterdir()):
        if d.is_dir() and _first_match(d, src_ext, recursive=True) is not None:
            dirs.append(d)
    return dirs


def collect_files(dirs, src_ext, exclude=None):
    """Flatten selected dirs into a deduped, sorted file list.

    Selected dirs often nest (Album and Album/Disc1). rglob from both yields
    the same file twice; converting it twice races two ffmpeg processes onto
    one destination. Dedupe here so every caller is safe.

    Files under `exclude` (the output dir, when it sits inside the source
    tree) are skipped, otherwise each run would re-convert its own output.
    """
    exc = os.path.normcase(str(exclude.resolve())) if exclude else None
    seen = set()
    out = []
    for d in dirs:
        for f in d.rglob("*"):
            if not _match(f, src_ext):
                continue
            if exc and os.path.normcase(str(f.resolve())).startswith(exc + os.sep):
                continue
            key = os.path.normcase(str(f.resolve()))
            if key in seen:
                continue
            seen.add(key)
            out.append(f)
    return sorted(out)


# --------------------------------------------------------------------------
# Conversion core
# --------------------------------------------------------------------------

def build_ffmpeg_cmd(src, dst, codec, codec_info, settings):
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
           # file-level parallelism is the bottleneck; one thread per process
           # avoids oversubscribing cores and is measurably faster in batches
           "-threads", "1", "-i", str(src)]
    # Always take the first audio stream only. Without this an m4a with cover
    # art maps the picture as video and the mp3 muxer rejects it.
    cmd += ["-map", "0:a:0", "-vn"]
    cmd += ["-map_metadata", "-1"] if settings.strip_meta else ["-map_metadata", "0"]
    cmd += ["-c:a", codec]
    cmd += quality_params(codec_info, settings.quality)[0]
    if settings.sample_rate > 0:
        cmd += ["-ar", str(min(settings.sample_rate, codec_info["max_sr"]))]
    if settings.channels > 0:
        cmd += ["-ac", str(settings.channels)]
    if settings.normalize:
        # single pass only: loudnorm's integrated loudness is an estimate here.
        # Two-pass is exact but doubles process count.
        cmd += ["-af", "loudnorm=I=-16:LRA=11:TP=-1.5"]
    # ALAC needs an explicit muxer: its menu extension is not a container.
    fmt = codec_info.get("container")
    if fmt:
        cmd += ["-f", fmt]
    cmd += [str(dst)]
    return cmd


def same_format(src, dst, settings):
    """True when re-encoding would only lose quality.

    Same-extension conversions are pointless unless the user asks for a
    real change (sample rate, channels, normalize) or passes --force.
    """
    if settings.force or settings.sample_rate or settings.channels or settings.normalize:
        return False
    return src.suffix.lower() == dst.suffix.lower()


def convert_file(src, dst, codec, codec_info, settings, display=None):
    """Convert one file. Returns (status, name, orig_size, new_size, detail).

    The original is unlinked only after ffmpeg exits 0 AND dst is non-empty.
    """
    name = display or get_short_name(src.stem)
    if src.resolve() == dst.resolve() or dst.exists():
        return "skip", name, 0, 0, ""
    if same_format(src, dst, settings):
        return "skip", name, 0, 0, "same format (use --force to re-encode)"
    try:
        orig_size = src.stat().st_size
    except OSError as e:
        return "fail", name, 0, 0, str(e)

    cmd = build_ffmpeg_cmd(src, dst, codec, codec_info, settings)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    except OSError as e:
        return "fail", name, orig_size, 0, str(e)

    if p.returncode != 0:
        try:
            dst.unlink(missing_ok=True)
        except OSError:
            pass
        return "fail", name, orig_size, 0, _ff_error(p, "ffmpeg")
    if not dst.exists():
        return "fail", name, orig_size, 0, tr("no_output")
    new_size = dst.stat().st_size
    if new_size == 0:
        try:
            dst.unlink()
        except OSError:
            pass
        return "fail", name, orig_size, 0, tr("no_output")

    if not settings.keep:
        try:
            src.unlink()
        except OSError:
            pass
    return "ok", name, orig_size, new_size, ""


def _ff_error(proc, label):
    err = (proc.stderr or "").strip().splitlines()
    return f"{label} {proc.returncode}: {err[-1]}" if err else f"{label} {proc.returncode}"


# --------------------------------------------------------------------------
# Batch runner with progress
# --------------------------------------------------------------------------

class Stats:
    def __init__(self, total):
        self.lock = threading.Lock()
        self.total = total
        self.done = self.ok = self.skip = self.fail = 0
        self.cur = ""
        self.stop = False

    def tick(self, status, name=""):
        with self.lock:
            self.done += 1
            self.cur = name
            if status == "ok":
                self.ok += 1
            elif status == "skip":
                self.skip += 1
            else:
                self.fail += 1

    def snapshot(self):
        with self.lock:
            return self.done, self.total, self.ok, self.skip, self.fail, self.cur


def _progress_loop(st, start):
    width = 34
    while not st.stop:
        d, t, ok, sk, fl, cur = st.snapshot()
        pct = (d / t * 100) if t else 100.0
        filled = int(width * pct / 100)
        el = time.time() - start
        rate = d / el if el > 0.01 and d else 0.0
        eta = (t - d) / rate if rate > 0 else 0.0
        bar = "█" * filled + "░" * (width - filled)
        line1 = (f"  {bar} {pct:5.1f}%  {d}/{t}  "
                 f"{fmt_dur(eta)} {tr('eta')}  {rate:.1f} {tr('per_sec')}")
        short = cur if len(cur) <= 52 else cur[:49] + "..."
        cprint("\r" + line1.ljust(100), "", "")
        cprint("\r" + f"  {C.CYN}↳{C.RST} {C.LG}{short}{C.RST}".ljust(100), "", "")
        time.sleep(0.1)
    sys.stdout.write("\r" + " " * 100 + "\r" + " " * 100 + "\r")
    sys.stdout.flush()


def run_batch(jobs, codec, codec_info, settings, threads=None):
    """jobs: list of (src, dst, display). Returns a summary dict."""
    total = len(jobs)
    threads = threads or max(1, (os.cpu_count() or 2) - 1)
    threads = max(1, min(threads, total))
    st = Stats(total)
    errors = []
    total_orig = total_new = 0
    interrupted = False
    start = time.time()

    cprint(f"\n  {C.BOLD}{C.CYN}╒ {tr('converting')} {total} {tr('files')} "
           f"({tr('threads')}: {threads}){C.RST}")

    pool = ThreadPoolExecutor(max_workers=threads)
    render = threading.Thread(target=_progress_loop, args=(st, start), daemon=True)
    fut = {}
    try:
        for src, dst, disp in jobs:
            fut[pool.submit(convert_file, src, dst, codec, codec_info, settings, disp)] = src
        render.start()
        for f in as_completed(fut):
            try:
                status, name, osz, nsz, detail = f.result()
            except Exception as e:  # noqa: BLE001 - one bad file must not kill the batch
                src = fut[f]
                status, name, osz, nsz, detail = "fail", src.stem, 0, 0, repr(e)
            st.tick(status, name)
            if status == "ok":
                total_orig += osz
                total_new += nsz
            elif status == "fail":
                errors.append((fut[f], detail))
    except KeyboardInterrupt:
        interrupted = True
        st.stop = True
        cprint(f"\n  {C.YLW}[!] {tr('interrupted')}{C.RST}")
        for f in fut:
            f.cancel()
    finally:
        st.stop = True
        pool.shutdown(wait=True)
        time.sleep(0.15)

    d, t, ok, sk, fl, _ = st.snapshot()
    elapsed = time.time() - start

    cprint(f"\n  {C.BOLD}{C.CYN}╕ {tr('summary_title')} / {tr('done')} {C.RST}")
    cprint(f"  {C.GRN}{ok} {tr('ok')}{C.RST}   "
           f"{C.YLW}{sk} {tr('skip')}{C.RST}   "
           f"{C.RED}{fl} {tr('fail')}{C.RST}   "
           f"{C.LG}{t - d} {tr('cancelled')}{C.RST}")
    cprint(f"  {tr('time')}: {fmt_dur(elapsed)}")
    if total_orig and total_new:
        delta = total_orig - total_new
        word = tr("smaller") if delta >= 0 else tr("larger")
        cprint(f"  {tr('size')}: {format_size(total_orig)} → {format_size(total_new)} "
               f"({format_size(abs(delta))} {word})")
    if not settings.keep:
        cprint(f"  {C.LG}{tr('deleted')}: {ok}{C.RST}")

    if errors:
        log = Path.cwd() / "hiyo-convert_errors.log"
        try:
            with open(log, "w", encoding="utf-8") as fh:
                for path, detail in errors:
                    fh.write(f"{path}\t{detail}\n")
            cprint(f"\n  {C.YLW}{tr('failed_list')} ({len(errors)}):{C.RST}")
            for path, detail in errors[:10]:
                cprint(f"    {C.RED}✗{C.RST} {path.name}  {C.LG}{detail}{C.RST}")
            if len(errors) > 10:
                cprint(f"    {C.LG}... +{len(errors) - 10}{C.RST}")
            cprint(f"  {C.LG}{tr('log_at')}: {log}{C.RST}")
        except OSError:
            pass

    return {"ok": ok, "skip": sk, "fail": fl, "cancelled": t - d,
            "elapsed": elapsed, "errors": errors, "interrupted": interrupted}


# --------------------------------------------------------------------------
# YouTube / Spotify
# --------------------------------------------------------------------------

SPOTIFY_PAGE = "https://open.spotify.com/embed/{kind}/{sid}"
_SPOTIFY_URL = re.compile(
    r"^https?://(?:open\.)?spotify\.com/(?:intl-[a-z-]+/)?"
    r"(track|album|playlist|episode|show)/([A-Za-z0-9]+)",
    re.IGNORECASE,
)
_SPOTIFY_SHORT = re.compile(r"^https?://spotify\.link/", re.IGNORECASE)
_YT_URL = re.compile(r"^https?://(?:[\w-]+\.)?(?:youtube\.com|youtu\.be)/", re.IGNORECASE)
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HiyoConvert/2.2"


def classify_url(url):
    """Return 'youtube', 'spotify' or None. Pure, so it is unit-tested."""
    if _YT_URL.match(url):
        return "youtube"
    if _SPOTIFY_URL.match(url) or _SPOTIFY_SHORT.match(url):
        return "spotify"
    return None


def _http_get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace"), resp.geturl()


def _spotify_parts(url):
    """Extract (kind, id) from a Spotify URL, following spotify.link."""
    if _SPOTIFY_SHORT.match(url):
        try:
            _, final = _http_get(url)
        except (urllib.error.URLError, OSError):
            return None, None
        url = final
    m = _SPOTIFY_URL.match(url)
    return (m.group(1), m.group(2)) if m else (None, None)


def _split_artists(subtitle):
    # Spotify's embed separates artists with a thin space (U+2009), often
    # after a real comma: "A,\u2009B". Normalize both forms to "A, B".
    text = (subtitle or "").strip()
    text = re.sub(r"\s*,\s*[\u2009\u00a0]\s*", ", ", text)
    return re.sub(r"[\u2009\u00a0]+", ", ", text)


def parse_spotify_embed(html):
    """Parse the __NEXT_DATA__ blob in a Spotify embed page.

    Works without an API key. Track pages carry one entity; album and
    playlist pages carry a trackList. Pure, so fixtures can test it.
    """
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        re.S,
    )
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
        entity = data["props"]["pageProps"]["state"]["data"]["entity"]
    except (ValueError, KeyError, TypeError):
        return []

    if entity.get("type") == "track":
        title = entity.get("title") or entity.get("name") or ""
        artists = [a.get("name") for a in entity.get("artists") or [] if a.get("name")]
        return [{"title": title, "artist": ", ".join(artists)}] if title else []

    tracks = []
    for item in entity.get("trackList") or []:
        title = item.get("title") or ""
        if title:
            tracks.append({"title": title, "artist": _split_artists(item.get("subtitle"))})
    return tracks


def spotify_metadata(url):
    """Resolve a track/album/playlist URL to [{title, artist}]."""
    kind, sid = _spotify_parts(url)
    if not kind:
        cprint(f"  {C.RED}[!] {tr('invalid_url')}: {url}{C.RST}")
        return []
    if kind in ("episode", "show"):
        cprint(f"  {C.RED}[!] Spotify {kind} URLs are not supported{C.RST}")
        return []
    try:
        html, _ = _http_get(SPOTIFY_PAGE.format(kind=kind, sid=sid))
    except (urllib.error.URLError, OSError) as e:
        cprint(f"  {C.RED}[{tr('fail')}]{C.RST} {url}  {C.LG}{e}{C.RST}")
        return []
    tracks = parse_spotify_embed(html)
    if not tracks:
        cprint(f"  {C.RED}[{tr('fail')}]{C.RST} {url}  {C.LG}no tracks in embed{C.RST}")
    return tracks


def _entry_filepath(info, temp_dir):
    """Find the file yt-dlp wrote for one video entry."""
    if not info:
        return None
    # ytsearch1: returns a playlist-shaped wrapper; unwrap its first video.
    entries = info.get("entries")
    if entries:
        for entry in entries:
            if entry:
                return _entry_filepath(entry, temp_dir)
        return None
    for item in info.get("requested_downloads") or []:
        path = item.get("filepath") or item.get("_filename")
        if path and Path(path).exists():
            return Path(path)
    vid = info.get("id") or ""
    ext = info.get("ext") or ""
    if vid and ext:
        path = temp_dir / f"{vid}.{ext}"
        if path.exists():
            return path
    if vid:
        return next(
            (f for f in temp_dir.iterdir() if f.is_file() and f.name.startswith(vid)),
            None,
        )
    return None


def download_youtube(url, temp_dir):
    """Download a video or every entry of a playlist. Returns [(path, title)]."""
    try:
        import yt_dlp
    except ImportError:
        cprint(f"  {C.RED}[!] pip install yt-dlp{C.RST}")
        return []
    out = str(temp_dir / "%(id)s.%(ext)s")
    opts = {"format": "bestaudio/best", "outtmpl": out, "quiet": True,
            "no_warnings": True, "noprogress": True, "noplaylist": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:  # noqa: BLE001 - yt_dlp raises many types
        cprint(f"  {C.RED}[{tr('fail')}]{C.RST} {url}  {C.LG}{e}{C.RST}")
        return []

    entries = list(info.get("entries") or [info]) if info else []
    got = []
    for entry in entries:
        if not entry:
            continue
        path = _entry_filepath(entry, temp_dir)
        if path:
            got.append((path, entry.get("title") or path.stem))
    if not got:
        cprint(f"  {C.RED}[{tr('fail')}]{C.RST} {url}  {C.LG}{tr('no_files')}{C.RST}")
    return got


def download_spotify(url, temp_dir, threads=None):
    """Resolve Spotify metadata, then fetch the best YouTube match per track.

    Spotify has no public audio API. The embed page gives title/artists;
    yt-dlp searches YouTube for each one. NOT Spotify's audio.
    """
    try:
        import yt_dlp
    except ImportError:
        cprint(f"  {C.RED}[!] pip install yt-dlp{C.RST}")
        return []

    tracks = spotify_metadata(url)
    if not tracks:
        return []
    cprint(f"  {C.LG}[Spotify] {len(tracks)}{C.RST}")

    out = str(temp_dir / "%(id)s.%(ext)s")
    opts = {"format": "bestaudio/best", "outtmpl": out, "quiet": True,
            "no_warnings": True, "noprogress": True}
    errors = []
    lock = threading.Lock()

    def one(track):
        title = track.get("title") or ""
        artist = track.get("artist") or ""
        query = f"{artist} - {title}" if artist else title
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"ytsearch1:{query}", download=True)
            path = _entry_filepath(info, temp_dir)
            if path:
                return (path, query)
            with lock:
                errors.append((query, tr("no_files")))
            return None
        except Exception as e:  # noqa: BLE001 - one track must not kill the list
            with lock:
                errors.append((query, str(e)[:200]))
            return None

    workers = threads or max(1, min(4, len(tracks)))
    workers = max(1, min(workers, len(tracks)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, tracks))
    got = [r for r in results if r]
    failed = len(results) - len(got)
    if failed:
        cprint(f"  {C.YLW}[!] {failed}/{len(results)} {tr('fail')}{C.RST}")
        for query, detail in errors[:3]:
            cprint(f"    {C.RED}✗{C.RST} {query}  {C.LG}{detail}{C.RST}")
    return got


def download_urls(urls, temp_dir, threads=None):
    got = []
    lock = threading.Lock()

    def one(item):
        kind, url = item
        if kind == "spotify":
            res = download_spotify(url, temp_dir, threads)
        else:
            res = download_youtube(url, temp_dir)
        for path, title in res:
            with lock:
                got.append((path, title))

    if not urls:
        return got
    cprint(f"\n  {C.BOLD}{C.CYN}╒ {tr('downloading')} ({len(urls)}){C.RST}")
    workers = threads or max(1, min(os.cpu_count() or 2, len(urls)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, urls))
    return got


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------

def pick_menu(items, prompt=""):
    for i, (label, *_) in enumerate(items, 1):
        cprint(f"  {C.BOLD}[{i}]{C.RST} {label}")
    cprint(f"  {C.BOLD}[Q]{C.RST} {tr('quit')}")
    while True:
        sel = ask(f"  {C.CYN}›{C.RST} {prompt}").strip().lower()
        if sel in ("q", "\x03"):
            raise Abort
        try:
            n = int(sel)
            if 1 <= n <= len(items):
                return items[n - 1]
        except ValueError:
            pass


def pick_directory():
    items = [(tr("choose_dir_curr"), 1), (tr("choose_dir_enter"), 2)]
    if sys.platform == "win32":
        items.append((tr("choose_dir_browse"), 3))
    cprint(f"\n  {C.BOLD}{C.CYN}╒ {tr('choose_dir_title')} {C.RST}")
    for i, (label, _) in enumerate(items, 1):
        cprint(f"  {C.BOLD}[{i}]{C.RST} {label}")
    cprint(f"  {C.BOLD}[Q]{C.RST} {tr('back_to_menu')}")
    while True:
        sel = ask(f"  {C.CYN}›{C.RST} ").strip().lower()
        if sel in ("q", "\x03"):
            raise Abort
        if sel == "1":
            return Path.cwd()
        if sel == "2":
            p = ask(f"  {C.CYN}›{C.RST} {tr('choose_dir_path')}: ").strip()
            if p and Path(p).expanduser().is_dir():
                return Path(p).expanduser().resolve()
            cprint(f"  {C.YLW}[!] {tr('choose_dir_invalid')}{C.RST}")
        elif sel == "3" and sys.platform == "win32":
            picked = _windows_folder_picker()
            if picked:
                return Path(picked).resolve()
            cprint(f"  {C.YLW}[!] {tr('choose_dir_invalid')}{C.RST}")


def _windows_folder_picker():
    script = (
        'Add-Type -AssemblyName System.Windows.Forms;'
        '$f=New-Object System.Windows.Forms.FolderBrowserDialog;'
        '$f.Description="Select a music folder";'
        'if($f.ShowDialog() -eq "OK"){Write-Output $f.SelectedPath}'
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           capture_output=True, text=True, timeout=20)
        return r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def choose_language():
    clear_screen()
    cprint(f"\n  {C.BOLD}{C.CYN}╒ HiyoConvert{C.RST}\n")
    cprint(f"  {C.LG}{LANG['en']['choose_lang']}{C.RST}")
    cprint(f"  {C.BOLD}[1]{C.RST} English")
    cprint(f"  {C.BOLD}[2]{C.RST} Tiếng Việt")
    cprint(f"  {C.BOLD}[Q]{C.RST} {LANG['en']['quit']}")
    while True:
        sel = ask("  › ").strip().lower()
        if sel == "1":
            return "en"
        if sel == "2":
            return "vi"
        if sel in ("q", "\x03"):
            raise Abort


def input_urls():
    cprint(f"\n  {C.BOLD}{C.CYN}╒ {tr('enter_urls')}{C.RST}")
    urls = []
    while True:
        line = ask("  › ").strip()
        if line == "\x03":
            raise Abort
        if not line:
            break
        if line.lower() in ("q", "quit"):
            raise Abort
        for part in re.split(r"[,\s]+", line):
            kind = classify_url(part)
            if kind:
                urls.append((kind, part))
            else:
                cprint(f"  {C.YLW}[!] {tr('invalid_url')}: {part}{C.RST}")
    if not urls:
        cprint(f"  {C.YLW}[!] {tr('no_urls')}{C.RST}")
        raise Abort
    return urls


def toggle(value):
    return not value


def show_banner():
    banner = r"""
$$   $$\ $$                      $$$$$$\                                                     $$\
$$ |  $$ |\__|                    $$  __$$\                                                    $$ |
$$ |  $$ |$$\ $$\   $$\  $$$$$$\  $$ /  \__| $$$$$$\  $$$$$$$\ $$\    $$\  $$$$$$\   $$$$$$\ $$$$$$\
$$$$$$$$ |$$ |$$ |  $$ |$$  __$$\ $$ |      $$  __$$\ $$  __$$\\$$\  $$  |$$  __$$\ $$  __$$\\_$$  _|
$$  __$$ |$$ |$$ |  $$ |$$ /  $$ |$$ |      $$ /  $$ |$$ |  $$ |\$$\$$  / $$$$$$$$ |$$ |  \__| $$ |
$$ |  $$ |$$ |$$ |  $$ |$$ |  $$ |$$ |  $$\ $$ |  $$ |$$ |  $$ | \$$$  /  $$   ____|$$ |       $$ |$$\
$$ |  $$ |$$ |\$$$$$$$ |\$$$$$$  |\$$$$$$  |\$$$$$$  |$$ |  $$ |  \$  /   \$$$$$$$\ $$ |       \$$$$  |
\__|  \__|\__| \____$$ | \______/  \______/  \______/ \__|  \__|   \_/     \_______|\__|        \____/
              $$\   $$ |
              \$$$$$$  |
               \______/"""
    for line in banner.splitlines():
        cprint(line, C.CYN)
        time.sleep(0.025)
    time.sleep(0.35)


def _flag(on, invert=False):
    return tr("no") if (on == invert) else tr("yes")


def draw_menu(settings, root, src_type, src_ext, tgt_ext, codec_info):
    cprint(f"  {C.BOLD}{C.CYN}╒ {tr('title')}{C.RST}")
    src_label = tr("src_url") if src_type == "url" else tr("src_local")
    cprint(f"  {C.BOLD}[S]{C.RST} {tr('current_src')}: {C.WHT}{src_label}{C.RST}")
    cprint(f"  {C.BOLD}[T]{C.RST} {tr('target')}:        "
           f"{C.WHT}{codec_info['name']}{C.RST}")
    if src_type == "local":
        filt = tr("all") if src_ext is None else src_ext
        cprint(f"  {C.BOLD}[F]{C.RST} {tr('src_filter')}: {C.WHT}{filt}{C.RST}")
    cprint(f"  {C.BOLD}[D]{C.RST} {tr('change_dir')}:  {C.WHT}{root}{C.RST}")
    lang_name = "Tiếng Việt" if L is LANG["vi"] else "English"
    cprint(f"  {C.BOLD}[L]{C.RST} {tr('language')}: {C.WHT}{lang_name}{C.RST}")
    cprint(f"\n  {C.BOLD}{C.CYN}╘ {tr('settings_hdr')}{C.RST}")
    _, qlabel = quality_params(codec_info, settings.quality)
    od = str(settings.output_dir) if settings.output_dir else tr("none")
    sr = f"{settings.sample_rate} Hz" if settings.sample_rate else tr("original")
    ch = {0: tr("original"), 1: "Mono", 2: "Stereo"}.get(settings.channels, "?")
    rows = [
        ("1", tr("quality"), f"{settings.quality}/10 {C.LG}({qlabel}){C.RST}"),
        ("2", tr("output_dir"), f"{C.LG}{od}{C.RST}"),
        ("3", tr("sample_rate"), f"{C.LG}{sr}{C.RST}"),
        ("4", tr("channels"), f"{C.LG}{ch}{C.RST}"),
        ("5", tr("normalize"), f"{C.MAG}{_flag(settings.normalize)}{C.RST}"),
        ("6", tr("strip_meta"), f"{C.MAG}{_flag(settings.strip_meta)}{C.RST}"),
        ("7", tr("simplify"), f"{C.MAG}{_flag(settings.simplify)}{C.RST}"),
        ("8", tr("strip_diacritics"), f"{C.MAG}{_flag(settings.strip_diacritics)}{C.RST}"),
        ("9", tr("keep"), f"{C.MAG}{_flag(settings.keep)}{C.RST}"),
        ("0", tr("dry_run"), f"{C.MAG}{_flag(settings.dry_run)}{C.RST}"),
    ]
    for key, label, val in rows:
        cprint(f"  {C.BOLD}[{key}]{C.RST} {label:<22} {val}")
    cprint(f"  {C.BOLD}{'─' * 46}{C.RST}")
    cprint(f"  {C.BOLD}[Enter]{C.RST} {C.CYN}{C.BOLD}{tr('start_now')}{C.RST}"
           f"        {C.BOLD}[Q]{C.RST} {tr('quit')}")


def edit_setting(settings, key, codec_info):
    if key == "1":
        try:
            v = int(ask(f"  {C.CYN}›{C.RST} 1-10 (10=best): ").strip())
            settings.quality = max(1, min(10, v))
        except ValueError:
            pass
    elif key == "2":
        p = ask(f"  {C.CYN}›{C.RST} {tr('choose_dir_path')} (blank = {tr('none')}): ").strip()
        settings.output_dir = Path(p).expanduser().resolve() if p else None
        if settings.output_dir:
            settings.output_dir.mkdir(parents=True, exist_ok=True)
    elif key == "3":
        item = pick_menu([(tr("original") if v == 0 else f"{v} Hz", v) for v in SAMPLE_RATES])
        settings.sample_rate = item[1]
    elif key == "4":
        item = pick_menu([(tr("original") if v == 0 else ("Mono" if v == 1 else "Stereo"), v)
                          for v in CHANNELS])
        settings.channels = item[1]
    elif key in ("5", "6", "7", "8", "9", "0"):
        attr = {"5": "normalize", "6": "strip_meta", "7": "simplify",
                "8": "strip_diacritics", "9": "keep", "0": "dry_run"}[key]
        setattr(settings, attr, toggle(getattr(settings, attr)))
    _ = codec_info


def confirm(total, src_ext, tgt_ext, codec_info, settings):
    cprint(f"\n  {C.BOLD}{C.CYN}╒═ {tr('confirm_title')}{C.RST}")
    srcs = tr("all") if src_ext is None else src_ext
    cprint(f"  {C.BOLD}{total}{C.RST} {tr('files')} {C.LG}{srcs}{C.RST} "
           f"→ {C.BOLD}{codec_info['name']}{C.RST}")
    _, qlabel = quality_params(codec_info, settings.quality)
    bits = [qlabel]
    if settings.sample_rate:
        sr = min(settings.sample_rate, codec_info["max_sr"])
        bits.append(f"{sr} Hz")
    if settings.channels:
        bits.append("Mono" if settings.channels == 1 else "Stereo")
    if settings.normalize:
        bits.append("loudnorm")
    cprint(f"  {C.LG}{' · '.join(bits)}{C.RST}")
    cprint(f"  {C.LG}→{C.RST} {settings.output_dir or tr('original')}")
    if settings.keep:
        cprint(f"  {C.GRN}✓{C.RST} {tr('confirm_warn_keep')}")
    else:
        cprint(f"  {C.YLW}{C.BOLD}⚠  {tr('confirm_warn_delete')}{C.RST}")
    cprint(f"  {C.LG}{tr('confirm_run')}{C.RST}")
    # strip: a stray space must not count as Enter on a screen that can
    # delete originals.
    return ask().strip() == ""


def run_local(settings, root, src_ext, tgt_ext, codec, codec_info, threads=None):
    cprint(f"  {tr('scanning')}...", C.DIM)
    dirs = spinner_task(tr("scanning"), scan_dirs, root, src_ext)
    files = collect_files(dirs, src_ext, exclude=settings.output_dir)
    if not files:
        cprint(f"  {C.YLW}[!] {tr('no_files')}{C.RST}")
        return
    cprint(f"  {tr('found')} {C.BOLD}{len(files)}{C.RST} {tr('files')}")
    jobs = plan_jobs(files, tgt_ext, settings, root)
    if settings.dry_run:
        preview(jobs, tgt_ext, settings)
        return
    if not confirm(len(jobs), src_ext, tgt_ext, codec_info, settings):
        raise Abort
    run_batch(jobs, codec, codec_info, settings, threads)


def preview(jobs, tgt_ext, settings):
    clear_screen()
    cprint(f"\n  {C.BOLD}{C.CYN}╒═ {tr('dry_run_title')}{C.RST}")
    cprint(f"  {tr('would_convert')} {len(jobs)} {tr('files')} {tr('to')} {tgt_ext}\n")
    for src, dst, _ in jobs[:400]:
        try:
            sz = format_size(src.stat().st_size)
        except OSError:
            sz = "?"
        try:
            rel = dst.relative_to(settings.output_dir) if settings.output_dir else dst
        except ValueError:
            rel = dst
        cprint(f"  {C.LG}[{sz:>8}]{C.RST} {src.name}  {C.LG}→{C.RST} {rel}")
    if len(jobs) > 400:
        cprint(f"  {C.LG}... +{len(jobs) - 400}{C.RST}")
    cprint(f"\n  {C.LG}{tr('confirm_warn_delete') if not settings.keep else tr('confirm_warn_keep')}{C.RST}")
    press_enter()


def run_url(settings, tgt_ext, codec, codec_info, threads=None):
    urls = input_urls()
    if settings.dry_run:
        clear_screen()
        cprint(f"\n  {C.BOLD}{C.CYN}╒═ {tr('dry_run_title')}{C.RST}")
        cprint(f"  {tr('would_convert')} {len(urls)} {tr('files')} {tr('to')} {tgt_ext}\n")
        for kind, url in urls:
            cprint(f"  {C.LG}[{kind}]{C.RST} {url}")
        press_enter()
        return
    if not confirm(len(urls), "URL", tgt_ext, codec_info, settings):
        raise Abort

    temp = Path(tempfile.mkdtemp(prefix="hiyoconvert_yt_"))
    try:
        got = download_urls(urls, temp, threads)
        if not got:
            cprint(f"  {C.YLW}[!] {tr('no_files')}{C.RST}")
            return
        clear_screen()
        jobs = plan_download_jobs(got, tgt_ext, settings)
        # Temp files are deleted in finally, so keep=True is only about the
        # size report; never mutate the user's saved setting.
        batch_settings = Settings.from_dict(settings.to_dict())
        batch_settings.keep = True
        batch_settings.force = settings.force
        run_batch(jobs, codec, codec_info, batch_settings, threads)
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def menu_loop():
    global L
    cfg = Settings.load()
    settings = cfg or Settings()
    lang = load_saved_lang()
    root = Path.cwd()
    src_ext = None
    src_type = "local"
    tgt_ext = ".mp3"

    clear_screen()
    show_banner()
    if lang:
        L = LANG[lang]
    else:
        lang = choose_language()
        L = LANG[lang]
        settings.save(lang)
    if not shutil.which("ffmpeg"):
        cprint(f"\n  {C.RED}[!] {tr('no_ffmpeg')}{C.RST}\n")
        press_enter()

    first = True
    while True:
        clear_screen()
        if not first:
            cprint(f"  {C.LG}{tr('back_to_menu')}{C.RST}")
            time.sleep(0.3)
            bounce_arrow(2)
        first = False
        fade_out()

        codec_info = CODECS[tgt_ext]
        codec = codec_info["codec"]
        while True:
            draw_menu(settings, root, src_type, src_ext, tgt_ext, codec_info)
            key = ask("  › ").strip().lower()
            if key in ("q", "\x03"):
                raise Abort
            if key == "":
                break
            if key == "s":
                src_type = "url" if src_type == "local" else "local"
                clear_screen()
            elif key == "t":
                item = pick_menu(TGT_FORMATS)
                tgt_ext = item[1]
                codec_info = CODECS[tgt_ext]
                codec = codec_info["codec"]
                clear_screen()
            elif key == "f" and src_type == "local":
                src_ext = pick_menu(SRC_FORMATS)[1]
                clear_screen()
            elif key == "d":
                root = pick_directory()
                clear_screen()
            elif key == "l":
                lang = choose_language()
                L = LANG[lang]
                settings.save(lang)
                clear_screen()
            else:
                edit_setting(settings, key, codec_info)

        settings.save(lang)
        clear_screen()
        try:
            if src_type == "url":
                run_url(settings, tgt_ext, codec, codec_info)
            else:
                run_local(settings, root, src_ext, tgt_ext, codec, codec_info)
        except Abort:
            clear_screen()
            continue
        press_enter()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="hiyo-convert",
        description="Batch audio converter. Run with no --to for the interactive menu.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("path", nargs="?", help="source directory")
    p.add_argument("--to", dest="to", help="target format: " + " ".join(CODECS.keys()))
    p.add_argument("--src", dest="src", help="only convert this source extension, e.g. .flac")
    p.add_argument("-q", "--quality", type=int, choices=range(1, 11), help="1-10, 10=best")
    p.add_argument("-o", "--out", help="output directory")
    p.add_argument("--sample-rate", type=int, choices=SAMPLE_RATES[1:], help="Hz")
    p.add_argument("--channels", type=int, choices=[1, 2], help="1=mono 2=stereo")
    p.add_argument("--normalize", action="store_true", help="EBU R128 loudnorm")
    p.add_argument("--strip-meta", action="store_true", help="drop all metadata")
    p.add_argument("--simplify", action="store_true", help="clean up filenames")
    p.add_argument("--strip-diacritics", action="store_true", help="remove Vietnamese tones")
    p.add_argument("--keep", action="store_true", help="keep originals (default: delete)")
    p.add_argument("--force", action="store_true", help="re-encode even when source and target formats match")
    p.add_argument("--dry-run", action="store_true", help="list what would happen")
    p.add_argument("-j", "--threads", type=int, help="parallel ffmpeg processes")
    p.add_argument("--lang", choices=["en", "vi"], help="UI language")
    p.add_argument("--config", action="store_true", help="print saved settings and exit")
    p.add_argument("--version", action="version", version=f"HiyoConvert {__version__}")
    return p


def run_cli(args):
    global L
    saved = Settings.load()
    lang = args.lang or load_saved_lang() or ("vi" if sys.platform != "win32" else "en")
    if args.config:
        L = LANG[lang]
        payload = (saved or Settings()).to_dict()
        payload["lang"] = lang
        print(json.dumps(payload, indent=2))
        return 0
    if not args.to:
        print("error: --to is required in CLI mode (or run with no arguments for the menu)",
              file=sys.stderr)
        return 2
    if not shutil.which("ffmpeg"):
        print(f"error: {tr('no_ffmpeg')}", file=sys.stderr)
        return 1

    L = LANG[lang]
    tgt_ext = args.to if args.to.startswith(".") else f".{args.to}"
    if tgt_ext not in CODECS:
        print(f"error: unknown target {tgt_ext}. Choose from: {' '.join(CODECS.keys())}",
              file=sys.stderr)
        return 2
    codec_info = CODECS[tgt_ext]

    root = Path(args.path).expanduser().resolve() if args.path else Path.cwd()
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2
    src_ext = args.src.lower() if args.src else None
    if src_ext and not src_ext.startswith("."):
        src_ext = f".{src_ext}"
    if src_ext and src_ext not in ALL_AUDIO_EXTS:
        print(f"error: unknown source format {src_ext}. Choose from: "
              f"{' '.join(sorted(ALL_AUDIO_EXTS))}", file=sys.stderr)
        return 2

    settings = saved or Settings()
    for flag, attr in (("quality", "quality"), ("normalize", "normalize"),
                       ("strip_meta", "strip_meta"), ("simplify", "simplify"),
                       ("strip_diacritics", "strip_diacritics"), ("keep", "keep"),
                       ("dry_run", "dry_run")):
        val = getattr(args, flag)
        if val is not None and val is not False:
            setattr(settings, attr, val)
    if args.force:
        settings.force = True
    if args.out:
        settings.output_dir = Path(args.out).expanduser().resolve()
    if args.sample_rate:
        settings.sample_rate = args.sample_rate
    if args.channels:
        settings.channels = args.channels
    if settings.output_dir and not settings.dry_run:
        settings.output_dir.mkdir(parents=True, exist_ok=True)

    cprint(f"  {tr('scanning')} {root}...", C.DIM)
    dirs = scan_dirs(root, src_ext)
    files = collect_files(dirs, src_ext, exclude=settings.output_dir)
    if not files:
        print(f"error: {tr('no_files')}", file=sys.stderr)
        return 1
    cprint(f"  {tr('found')} {len(files)} {tr('files')}")

    jobs = plan_jobs(files, tgt_ext, settings, root)
    if settings.dry_run:
        preview(jobs, tgt_ext, settings)
        return 0
    if settings.normalize and codec_info["codec"] in ("pcm_s16le", "alac", "flac"):
        print("note: --normalize on a lossless codec changes the audio, not just its level",
              file=sys.stderr)
    summary = run_batch(jobs, codec_info["codec"], codec_info, settings, args.threads)
    return 1 if summary["fail"] else 0


def main():
    args = build_parser().parse_args()
    if args.to or args.config:
        sys.exit(run_cli(args))
    try:
        menu_loop()
    except Abort:
        clear_screen()
        cprint(f"\n  {C.LG}{tr('done')}{C.RST}\n")
    except KeyboardInterrupt:
        clear_screen()
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
