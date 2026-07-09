#!/usr/bin/env python3
"""
Terminal Monitor —— LLM CLI 终端权限提示自动响应工具。

══════════════════════════════════════════════════════════════════════════
  双模式检测：画面变化检测（默认，零依赖）+ OCR 检测（tesseract，更准）
  不依赖 LLM CLI 内部 hook。
══════════════════════════════════════════════════════════════════════════

用法:
  python terminal_monitor.py                     # 前台运行
  python terminal_monitor.py --once              # 检测一次
  python terminal_monitor.py --select-window     # 选择窗口
  python terminal_monitor.py --detect diff       # 强制画面变化检测
  python terminal_monitor.py --detect ocr        # 强制 OCR 检测

快捷键:
  Ctrl+Shift+A    切换监控开关
  Ctrl+Shift+X    立即响应一次
"""

import argparse
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from PIL import Image
import numpy as np

# ── Windows 编码兼容 ─────────────────────────────────────────────────
if sys.platform == "win32":
    sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1)
    sys.stderr = open(sys.stderr.fileno(), mode="w", encoding="utf-8", buffering=1)

logger = logging.getLogger("term_monitor")

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "config.yaml"


# ═══════════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════════

def load_config(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        logger.warning("配置文件 %s 不存在，使用默认值", path)
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_monitor_config(config: dict) -> dict:
    mc = config.get("monitor", {})
    return {
        "detect_method": mc.get("detect_method", "diff"),
        "scan_interval": mc.get("scan_interval", 1.5),
        "mode": mc.get("mode", "always_yes"),
        "hotkey_toggle": mc.get("hotkey_toggle", "ctrl+shift+a"),
        "hotkey_once": mc.get("hotkey_once", "ctrl+shift+x"),
        "focus_window": mc.get("focus_window", True),
        "screenshot_bottom_ratio": mc.get("screenshot_bottom_ratio", 0.4),
        # 画面变化检测参数
        "diff_threshold": mc.get("diff_threshold", 0.03),
        "diff_stable_frames": mc.get("diff_stable_frames", 2),
        "diff_cooldown": mc.get("diff_cooldown", 5.0),
        # OCR 参数
        "min_confidence": mc.get("min_confidence", 1),
        "prompt_patterns": mc.get("prompt_patterns", [
            r"\[y/n\]", r"\(y/n\)", r"yes/no", r"\[Y/n\]", r"\[y/N\]",
            r"Do you want to proceed", r"Allow this tool",
            r"Proceed\?", r"Continue\?", r"Accept", r"Confirm",
            r"是否", r"确认", r"允许",
        ]),
        "tesseract_cmd": mc.get("tesseract_cmd", None),
        "tesseract_lang": mc.get("tesseract_lang", "eng+chi_sim"),
        "window_title_contains": mc.get("window_title_contains", "llm"),
        "window_geometry": mc.get("window_geometry", None),
        "debug_screenshot_dir": mc.get("debug_screenshot_dir", None),
    }


# ═══════════════════════════════════════════════════════════════════════
# 窗口定位
# ═══════════════════════════════════════════════════════════════════════

def _get_window_geometry_pygetwindow(title_contains: str) -> Optional[dict]:
    try:
        import pygetwindow as gw
        windows = gw.getWindowsWithTitle(title_contains)
        if not windows:
            all_windows = gw.getAllWindows()
            for w in all_windows:
                if title_contains.lower() in w.title.lower() and w.visible:
                    windows = [w]
                    break
        if windows:
            w = windows[0]
            logger.debug("窗口: \"%s\" (%dx%d @ %d,%d)", w.title, w.width, w.height, w.left, w.top)
            return {"left": w.left, "top": w.top, "width": w.width, "height": w.height}
    except ImportError:
        logger.debug("pygetwindow 未安装")
    except Exception as e:
        logger.debug("查找窗口失败: %s", e)
    return None


def _get_active_window_geometry() -> Optional[dict]:
    try:
        import pygetwindow as gw
        w = gw.getActiveWindow()
        if w:
            return {"left": w.left, "top": w.top, "width": w.width, "height": w.height}
    except Exception:
        pass
    return None


def get_window_geometry(cfg: dict) -> Optional[dict]:
    if cfg.get("window_geometry"):
        return cfg["window_geometry"]
    geo = _get_window_geometry_pygetwindow(cfg.get("window_title_contains", "llm"))
    if geo:
        return geo
    return _get_active_window_geometry()


# ═══════════════════════════════════════════════════════════════════════
# 键盘模拟
# ═══════════════════════════════════════════════════════════════════════

def simulate_accept():
    """模拟键盘操作接受权限提示。
    用 SendInput + KEYEVENTF_UNICODE 直接发送 WM_CHAR，绕过中文输入法。
    先发 y（应对 [y/n] 提示），再发 Enter（应对选择型提示）。"""
    try:
        if sys.platform == "win32":
            _send_key_unicode("y")
        else:
            import pyautogui
            pyautogui.typewrite("y", interval=0.02)
        time.sleep(0.05)
        import pyautogui
        pyautogui.press("enter")
        logger.info("✔ 已发送 y + Enter")
    except Exception as e:
        logger.error("键盘模拟失败: %s", e)


def _send_key_unicode(ch: str):
    """用 SendInput + KEYEVENTF_UNICODE 发送单个字符，绕过 IME。"""
    import ctypes
    from ctypes import wintypes

    INPUT_KEYBOARD = 1
    KEYEVENTF_UNICODE = 0x0004
    KEYEVENTF_KEYUP = 0x0002

    class KBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", KBDINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]

    def _make_input(c, flags):
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.u.ki.wVk = 0
        inp.u.ki.wScan = ord(c)
        inp.u.ki.dwFlags = flags
        inp.u.ki.time = 0
        inp.u.ki.dwExtraInfo = ctypes.pointer(ctypes.c_ulong(0))
        return inp

    inputs = (INPUT * 2)(
        _make_input(ch, KEYEVENTF_UNICODE),                       # key down
        _make_input(ch, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),     # key up
    )
    user32 = ctypes.windll.user32
    user32.SendInput(2, ctypes.byref(inputs), ctypes.sizeof(INPUT))


def simulate_deny():
    try:
        import pyautogui
        pyautogui.press("tab")
        time.sleep(0.05)
        pyautogui.press("enter")
        logger.info("✘ 已发送 Tab+Enter")
    except Exception as e:
        logger.error("键盘模拟失败: %s", e)


def focus_window(geometry: dict):
    """将焦点移到目标终端窗口，确保后续键盘输入能命中。"""
    try:
        import pyautogui
        center_x = geometry["left"] + geometry["width"] // 2
        center_y = geometry["top"] + geometry["height"] // 2
        # 先移动到窗口再点击，确保焦点转移
        pyautogui.moveTo(center_x, center_y, duration=0.05)
        pyautogui.click()
        time.sleep(0.15)  # 给系统一点时间切换焦点
    except Exception as e:
        logger.debug("聚焦窗口失败: %s", e)


# ═══════════════════════════════════════════════════════════════════════
# 截图
# ═══════════════════════════════════════════════════════════════════════

def capture_bottom(geometry: dict, bottom_ratio: float) -> Image.Image:
    """截取窗口底部指定比例的区域，返回 PIL Image。"""
    import pyautogui
    left = geometry["left"]
    top = geometry["top"]
    width = geometry["width"]
    height = geometry["height"]
    crop_top = top + int(height * (1 - bottom_ratio))
    crop_height = int(height * bottom_ratio)
    return pyautogui.screenshot(region=(left, crop_top, width, crop_height))


# ═══════════════════════════════════════════════════════════════════════
# 检测方法一：画面变化检测（默认，零额外依赖）
# ═══════════════════════════════════════════════════════════════════════

class DiffDetector:
    """
    画面变化检测器。

    原理：
    1. 终端平时画面稳定（只有文本逐行输出）
    2. 权限提示出现时，底部会突然出现一个对话框/菜单
    3. 这个变化会导致底部截图的前后差异显著增大
    4. 连续 N 帧检测到变化 → 判定为提示 → 自动响应

    为了避免误触发（比如命令输出大量文本），会检查：
    - 变化区域是否集中在底部（提示总在底部）
    - 变化持续存在（不是一闪而过）
    - 冷却期内不重复触发
    """

    def __init__(self, threshold: float = 0.03, stable_frames: int = 2, cooldown: float = 5.0):
        self.threshold = threshold          # 像素变化比例阈值
        self.stable_frames = stable_frames  # 连续多少帧变化才算稳定
        self.cooldown = cooldown            # 触发后冷却时间（秒）
        self._prev_img: Optional[np.ndarray] = None
        self._change_count = 0
        self._last_trigger_time = 0.0
        self._baseline_img: Optional[np.ndarray] = None
        self._baseline_diff: float = 0.0

    def set_baseline(self, img: Image.Image):
        """设置基准画面（正常状态的终端底部）。"""
        self._baseline_img = np.array(img.convert("L"), dtype=np.float32)
        # 基准画面自身的"噪声"水平
        self._baseline_diff = 0.0

    def check(self, img: Image.Image) -> tuple[bool, float]:
        """
        检查是否有显著变化。
        返回 (is_prompt, change_ratio)。
        """
        now = time.time()
        current = np.array(img.convert("L"), dtype=np.float32)

        # 冷却期检查
        if self._last_trigger_time > 0 and (now - self._last_trigger_time) < self.cooldown:
            self._prev_img = current
            return False, 0.0

        if self._prev_img is None:
            self._prev_img = current
            if self._baseline_img is None:
                self.set_baseline(img)
            return False, 0.0

        # 计算与上一帧的差异比例
        diff = np.abs(current - self._prev_img)
        changed_pixels = np.sum(diff > 30)  # 灰度差 > 30 算变化
        total_pixels = current.size
        change_ratio = changed_pixels / total_pixels if total_pixels > 0 else 0

        # 同时检查与基准画面的差异
        baseline_change = 0.0
        if self._baseline_img is not None:
            base_diff = np.abs(current - self._baseline_img)
            baseline_change = np.sum(base_diff > 30) / total_pixels

        self._prev_img = current

        # 判断逻辑：
        # 1. 当前变化超过阈值
        # 2. 连续 stable_frames 帧都有变化
        # 3. 与基准画面也有显著差异（排除持续的滚动输出）
        is_changed = change_ratio > self.threshold

        if is_changed:
            self._change_count += 1
            logger.debug("变化检测: frame %d/%d, ratio=%.4f, base_ratio=%.4f",
                        self._change_count, self.stable_frames, change_ratio, baseline_change)
        else:
            # 画面恢复稳定 → 更新基准
            self._change_count = 0
            if change_ratio < self.threshold * 0.3:
                self._baseline_img = current.copy()

        if self._change_count >= self.stable_frames and baseline_change > self.threshold:
            self._change_count = 0
            self._last_trigger_time = now
            logger.info("变化检测触发! ratio=%.4f, base_ratio=%.4f", change_ratio, baseline_change)
            return True, change_ratio

        return False, change_ratio


# ═══════════════════════════════════════════════════════════════════════
# 检测方法二：OCR 检测（需 tesseract）
# ═══════════════════════════════════════════════════════════════════════

_tesseract_available: Optional[bool] = None


def init_tesseract(custom_path: Optional[str] = None) -> bool:
    global _tesseract_available
    if _tesseract_available is not None:
        return _tesseract_available
    try:
        import pytesseract
        if custom_path:
            pytesseract.pytesseract.tesseract_cmd = custom_path
        pytesseract.get_tesseract_version()
        _tesseract_available = True
        logger.info("Tesseract OCR 已就绪")
        return True
    except ImportError:
        logger.debug("pytesseract 未安装")
        _tesseract_available = False
        return False
    except Exception as e:
        logger.debug("Tesseract 不可用: %s", e)
        _tesseract_available = False
        return False


def ocr_detect_prompt(img: Image.Image, patterns: list[str],
                      lang: str = "eng+chi_sim",
                      min_confidence: int = 1) -> tuple[bool, list[str]]:
    """OCR 识别图片中的文字，检测权限提示。"""
    import pytesseract
    try:
        text = pytesseract.image_to_string(img, lang=lang, config="--psm 6").strip()
    except Exception as e:
        logger.debug("OCR 失败: %s", e)
        return False, []

    if not text:
        return False, []

    matched = []
    for pattern in patterns:
        try:
            if re.search(pattern, text, re.IGNORECASE):
                matched.append(pattern)
        except re.error:
            logger.warning("无效正则: %s", pattern)

    if len(matched) >= min_confidence:
        logger.debug("OCR 匹配: %s ← \"%s\"", matched, text[:200])
        return True, matched

    return False, []


# ═══════════════════════════════════════════════════════════════════════
# LLM 审查（可选）
# ═══════════════════════════════════════════════════════════════════════

def llm_review(text: str, config: dict) -> tuple[bool, str]:
    try:
        from llm_checker import LLMChecker
        checker = LLMChecker(config)
        return checker.check(text)
    except Exception as e:
        logger.warning("LLM 审查失败: %s，默认放行", e)
        return True, f"fallback allow: {e}"


# ═══════════════════════════════════════════════════════════════════════
# 全局状态
# ═══════════════════════════════════════════════════════════════════════

class MonitorState:
    def __init__(self):
        self.running = True
        self.monitoring = True
        self.respond_count = 0
        self.last_response_time: Optional[datetime] = None
        self._lock = threading.Lock()

    def toggle(self):
        with self._lock:
            self.monitoring = not self.monitoring
            status = "ON" if self.monitoring else "OFF"
            logger.info("═══════════════════════════════════")
            logger.info("  监控已 %s  (共响应 %d 次)", status, self.respond_count)
            logger.info("═══════════════════════════════════")
            return self.monitoring

    def record_response(self):
        with self._lock:
            self.respond_count += 1
            self.last_response_time = datetime.now()


# ═══════════════════════════════════════════════════════════════════════
# 主循环
# ═══════════════════════════════════════════════════════════════════════

def monitor_loop(config: dict, state: MonitorState):
    cfg = get_monitor_config(config)
    scan_interval = cfg["scan_interval"]
    detect_method = cfg["detect_method"]
    mode = cfg["mode"]
    bottom_ratio = cfg["screenshot_bottom_ratio"]
    debug_dir = cfg["debug_screenshot_dir"]

    # 初始化检测器
    diff_detector = None
    ocr_available = False

    if detect_method in ("diff", "auto"):
        diff_detector = DiffDetector(
            threshold=cfg["diff_threshold"],
            stable_frames=cfg["diff_stable_frames"],
            cooldown=cfg["diff_cooldown"],
        )
        logger.info("检测方式: 画面变化检测 (threshold=%.3f, frames=%d)",
                    cfg["diff_threshold"], cfg["diff_stable_frames"])

    if detect_method in ("ocr", "auto"):
        if init_tesseract(cfg.get("tesseract_cmd")):
            ocr_available = True
            logger.info("检测方式: OCR (tesseract)")
        elif detect_method == "ocr":
            logger.error("OCR 模式需要 tesseract。请安装后重试。")
            logger.error("  pip install pytesseract")
            logger.error("  + 安装 Tesseract-OCR: https://github.com/UB-Mannheim/tesseract/wiki")
            return

    if detect_method == "auto" and not ocr_available:
        logger.info("OCR 不可用，仅使用画面变化检测。")
        logger.info("（安装 tesseract 后可使用更精确的 OCR 检测）")

    import pyautogui
    pyautogui.FAILSAFE = True

    # 建立基准画面
    geometry = get_window_geometry(cfg)
    if geometry and diff_detector:
        try:
            baseline = capture_bottom(geometry, bottom_ratio)
            diff_detector.set_baseline(baseline)
            logger.info("基准画面已记录 (窗口: %dx%d)", geometry["width"], geometry["height"])
        except Exception as e:
            logger.warning("基准画面记录失败: %s", e)

    logger.info("终端监控已启动")
    logger.info("  扫描间隔: %.1fs | 模式: %s", scan_interval, mode)
    logger.info("  热键: %s (切换) | %s (单次)", cfg["hotkey_toggle"], cfg["hotkey_once"])

    consecutive_failures = 0
    max_failures = 10
    ocr_check_interval = 3  # OCR 每隔 N 次扫描才跑一次（节省性能）
    scan_count = 0

    while state.running:
        try:
            if not state.monitoring:
                time.sleep(0.5)
                continue

            geometry = get_window_geometry(cfg)
            if not geometry:
                time.sleep(scan_interval)
                continue

            img = capture_bottom(geometry, bottom_ratio)
            scan_count += 1
            detected = False
            detection_source = ""

            # ── 画面变化检测 ──
            if diff_detector:
                is_prompt, ratio = diff_detector.check(img)
                if is_prompt:
                    detected = True
                    detection_source = f"diff(ratio={ratio:.4f})"

            # ── OCR 检测（低频） ──
            if not detected and ocr_available and scan_count % ocr_check_interval == 0:
                is_prompt, matched = ocr_detect_prompt(
                    img, cfg["prompt_patterns"], cfg["tesseract_lang"], cfg["min_confidence"]
                )
                if is_prompt:
                    detected = True
                    detection_source = f"ocr({matched})"

            if detected:
                logger.info("🔔 检测到权限提示！[%s]", detection_source)

                # 保存调试截图
                if debug_dir:
                    try:
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                        Path(debug_dir).mkdir(parents=True, exist_ok=True)
                        img.save(str(Path(debug_dir) / f"prompt_{ts}.png"))
                    except Exception:
                        pass

                # 决策
                should_allow = True
                reason = "always_yes"

                if mode == "llm":
                    # 尝试用 OCR 提取文字发给 LLM
                    review_text = f"Diff detection: {detection_source}"
                    if ocr_available:
                        try:
                            import pytesseract
                            review_text = pytesseract.image_to_string(
                                img, lang=cfg["tesseract_lang"], config="--psm 6"
                            ).strip() or review_text
                        except Exception:
                            pass
                    should_allow, reason = llm_review(review_text, config)

                # 执行
                if should_allow:
                    if cfg["focus_window"]:
                        focus_window(geometry)
                    simulate_accept()
                    state.record_response()
                    logger.info("  #%d ✓ 自动接受 | %s", state.respond_count, reason)
                else:
                    logger.warning("  ✗ 拒绝 | %s", reason)

                # 重置检测器状态
                if diff_detector:
                    diff_detector._change_count = 0
                    diff_detector._last_trigger_time = time.time()

            consecutive_failures = 0
            time.sleep(scan_interval)

        except pyautogui.FailSafeException:
            logger.error("触发 FailSafe，监控已暂停")
            state.monitoring = False
        except KeyboardInterrupt:
            break
        except Exception as e:
            consecutive_failures += 1
            logger.warning("循环异常 (%d/%d): %s", consecutive_failures, max_failures, e)
            if consecutive_failures >= max_failures:
                logger.error("连续失败 %d 次，退出", max_failures)
                break
            time.sleep(scan_interval * 2)

    logger.info("监控已停止。共响应 %d 次。", state.respond_count)


# ═══════════════════════════════════════════════════════════════════════
# 热键监听
# ═══════════════════════════════════════════════════════════════════════

def start_hotkey_listener(state: MonitorState, hotkey_toggle: str, hotkey_once: str):
    try:
        from pynput import keyboard as pynput_keyboard
    except ImportError:
        logger.warning("pynput 未安装，热键不可用")
        return None

    def parse_hotkey(key_str: str) -> frozenset:
        keys = set()
        for p in key_str.strip().lower().split("+"):
            p = p.strip()
            if p in ("ctrl", "control"):
                keys.add(pynput_keyboard.Key.ctrl)
            elif p == "shift":
                keys.add(pynput_keyboard.Key.shift)
            elif p == "alt":
                keys.add(pynput_keyboard.Key.alt)
            elif len(p) == 1:
                keys.add(pynput_keyboard.KeyCode.from_char(p))
        return frozenset(keys)

    toggle_keys = parse_hotkey(hotkey_toggle)
    once_keys = parse_hotkey(hotkey_once)
    current_keys = set()

    def on_press(key):
        current_keys.add(key)
        cf = frozenset(current_keys)
        if cf == toggle_keys:
            state.toggle()
        elif cf == once_keys:
            logger.info("热键: 单次响应")
            threading.Thread(target=_run_once_check, args=(state,), daemon=True).start()

    def on_release(key):
        current_keys.discard(key)

    listener = pynput_keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.daemon = True
    listener.start()
    logger.info("热键监听已启动")
    return listener


def _run_once_check(state: MonitorState):
    """热键触发的单次检测。"""
    config = load_config(DEFAULT_CONFIG)
    cfg = get_monitor_config(config)
    import pyautogui
    geometry = get_window_geometry(cfg)
    if not geometry:
        return
    img = capture_bottom(geometry, cfg["screenshot_bottom_ratio"])
    # 使用 OCR 或简单触发
    if init_tesseract(cfg.get("tesseract_cmd")):
        is_prompt, matched = ocr_detect_prompt(
            img, cfg["prompt_patterns"], cfg["tesseract_lang"], cfg["min_confidence"]
        )
        if is_prompt:
            logger.info("单次: 发现提示 %s", matched)
            if cfg["focus_window"]:
                focus_window(geometry)
            simulate_accept()
            state.record_response()
        else:
            logger.info("单次: 未发现提示")
    else:
        # 没有 OCR，直接按 Enter（用户手动触发的，信任用户判断）
        if cfg["focus_window"]:
            focus_window(geometry)
        simulate_accept()
        state.record_response()
        logger.info("单次: 已发送 Enter")


# ═══════════════════════════════════════════════════════════════════════
# 单次模式
# ═══════════════════════════════════════════════════════════════════════

def run_once(config: dict):
    cfg = get_monitor_config(config)
    import pyautogui
    geometry = get_window_geometry(cfg)
    if not geometry:
        print("未找到目标窗口。")
        sys.exit(1)

    print(f"目标窗口: {geometry}")
    img = capture_bottom(geometry, cfg["screenshot_bottom_ratio"])
    print(f"截图: {img.size}")

    # OCR 检测
    if init_tesseract(cfg.get("tesseract_cmd")):
        is_prompt, matched = ocr_detect_prompt(
            img, cfg["prompt_patterns"], cfg["tesseract_lang"], cfg["min_confidence"]
        )
        if is_prompt:
            print(f"检测到提示！匹配: {matched}")
            if cfg["focus_window"]:
                focus_window(geometry)
            simulate_accept()
            print("已自动接受。")
        else:
            print("未检测到提示（OCR）。")
    else:
        print("OCR 不可用。使用 --detect diff 模式或安装 tesseract。")

    # 也显示 diff 检测结果
    detector = DiffDetector(threshold=cfg["diff_threshold"])
    detector.set_baseline(img)
    # 再截一帧做对比
    time.sleep(0.5)
    img2 = capture_bottom(geometry, cfg["screenshot_bottom_ratio"])
    is_change, ratio = detector.check(img2)
    print(f"画面变化: {'是' if is_change else '否'} (ratio={ratio:.4f}, threshold={cfg['diff_threshold']})")


# ═══════════════════════════════════════════════════════════════════════
# 窗口选择
# ═══════════════════════════════════════════════════════════════════════

def select_window() -> Optional[dict]:
    try:
        import pygetwindow as gw
        windows = [w for w in gw.getAllWindows() if w.visible and w.title.strip()]
        for i, w in enumerate(windows):
            print(f"  [{i}] {w.title[:80]}  ({w.width}x{w.height})")
        choice = input(f"\n输入窗口编号 (0-{len(windows)-1}): ").strip()
        idx = int(choice)
        if 0 <= idx < len(windows):
            w = windows[idx]
            geo = {"left": w.left, "top": w.top, "width": w.width, "height": w.height}
            print(f"已选择: {w.title}")
            print(f"几何信息: {geo}")
            save_window_config(geo)
            return geo
    except ImportError:
        print("请安装 pygetwindow")
    except (ValueError, IndexError):
        print("无效选择")
    except Exception as e:
        print(f"错误: {e}")
    return None


def save_window_config(geometry: dict):
    config = load_config(DEFAULT_CONFIG)
    config.setdefault("monitor", {})["window_geometry"] = geometry
    with open(DEFAULT_CONFIG, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    print(f"窗口配置已保存到 {DEFAULT_CONFIG}")


# ═══════════════════════════════════════════════════════════════════════
# 日志
# ═══════════════════════════════════════════════════════════════════════

def setup_logging(config: dict):
    log_cfg = config.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    fmt = logging.Formatter(
        "[%(asctime)s] %(name)-12s %(levelname)-8s %(message)s", datefmt="%H:%M:%S"
    )
    root = logging.getLogger("term_monitor")
    root.setLevel(level)
    root.handlers.clear()
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(fmt)
    root.addHandler(h)
    log_file = log_cfg.get("file", "")
    if log_file:
        p = Path(log_file) if Path(log_file).is_absolute() else SCRIPT_DIR / log_file
        h2 = logging.FileHandler(str(p), encoding="utf-8")
        h2.setFormatter(fmt)
        root.addHandler(h2)


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Terminal Monitor —— LLM CLI 权限提示自动响应工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python terminal_monitor.py                      # 前台运行（画面变化检测）
  python terminal_monitor.py --once               # 检测一次
  python terminal_monitor.py --select-window      # 选择要监控的窗口
  python terminal_monitor.py --detect diff        # 强制画面变化检测
  python terminal_monitor.py --detect ocr         # 强制 OCR 检测（需 tesseract）
  python terminal_monitor.py --detect auto        # 自动选择（默认）
        """,
    )
    parser.add_argument("-c", "--config", default=str(DEFAULT_CONFIG), help="配置文件路径")
    parser.add_argument("--detect", choices=["diff", "ocr", "auto"], default="auto",
                        help="检测方式: diff=画面变化, ocr=文字识别, auto=自动")
    parser.add_argument("--mode", choices=["always_yes", "llm", "llm_paranoid"],
                        help="覆盖监控模式")
    parser.add_argument("--once", action="store_true", help="只检测一次")
    parser.add_argument("--select-window", action="store_true", help="选择监控窗口")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")

    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path
    config = load_config(str(config_path))

    if args.verbose:
        config.setdefault("logging", {})["level"] = "DEBUG"
    setup_logging(config)

    if args.mode:
        config.setdefault("monitor", {})["mode"] = args.mode
    config.setdefault("monitor", {})["detect_method"] = args.detect

    if args.select_window:
        select_window()
        return

    if args.once:
        run_once(config)
        return

    # 前台监控
    state = MonitorState()
    cfg = get_monitor_config(config)
    start_hotkey_listener(state, cfg["hotkey_toggle"], cfg["hotkey_once"])

    try:
        monitor_loop(config, state)
    except KeyboardInterrupt:
        logger.info("收到退出信号")
    finally:
        state.running = False


if __name__ == "__main__":
    main()
