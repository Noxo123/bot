import time
import threading
from pathlib import Path

import cv2
import mss
import numpy as np
from pynput import keyboard
from pynput.keyboard import Controller
from plyer import notification

# ============================================================
# FiveM Fishing Bot
# F8 toggles the bot. F10 exits.
# Put screenshots/templates named Z.png, Q.png, S.png, D.png
# in templates/ if you want exact key recognition.
# ============================================================

DELAY_AFTER_DETECTION = 5.0
SCAN_INTERVAL = 0.08
MATCH_THRESHOLD = 0.72
TEMPLATE_DIR = Path(__file__).parent / "templates"

keyboard_controller = Controller()
sct = mss.mss()
running = False
exiting = False
busy = False


def notify(title: str, message: str):
    try:
        notification.notify(title=title, message=message, app_name="Noxo Fishing Bot", timeout=2)
    except Exception:
        print(f"[{title}] {message}")


def center_region():
    """Small central region; percentage-based so it works on different resolutions."""
    monitor = sct.monitors[1]
    width = monitor["width"]
    height = monitor["height"]
    region_w = int(width * 0.28)
    region_h = int(height * 0.22)
    return {
        "left": monitor["left"] + (width - region_w) // 2,
        "top": monitor["top"] + (height - region_h) // 2,
        "width": region_w,
        "height": region_h,
    }


def screenshot(region):
    frame = np.array(sct.grab(region))
    return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)


def load_templates():
    templates = {}
    TEMPLATE_DIR.mkdir(exist_ok=True)
    for key in "ZQSD":
        path = TEMPLATE_DIR / f"{key}.png"
        if path.exists():
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if image is not None and image.size:
                templates[key] = image
    return templates


def match_key(frame, templates):
    """Template matching. Returns (key, score), or (None, 0)."""
    if not templates:
        return None, 0.0

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    best_key, best_score = None, 0.0

    for key, template in templates.items():
        th, tw = template.shape[:2]
        if gray.shape[0] < th or gray.shape[1] < tw:
            continue
        result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, _ = cv2.minMaxLoc(result)
        if score > best_score:
            best_key, best_score = key, float(score)

    if best_score >= MATCH_THRESHOLD:
        return best_key, best_score
    return None, best_score


def visual_prompt_present(frame):
    """Fallback detector: detects a bright/high-contrast UI element in the center.
    It intentionally does not press a key without an exact template match.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, threshold = cv2.threshold(gray, 205, 255, cv2.THRESH_BINARY)
    ratio = cv2.countNonZero(threshold) / threshold.size
    return 0.002 < ratio < 0.45


def wait_until_prompt_disappears(region, templates, timeout=15):
    started = time.monotonic()
    while running and time.monotonic() - started < timeout:
        frame = screenshot(region)
        key, score = match_key(frame, templates)
        if key is None and not visual_prompt_present(frame):
            return True
        time.sleep(SCAN_INTERVAL)
    return False


def worker():
    global busy
    region = center_region()
    templates = load_templates()

    if templates:
        notify("Noxo Fishing Bot", f"Prêt — templates chargés : {', '.join(templates)}")
    else:
        notify("Noxo Fishing Bot", "Aucun template Z/Q/S/D. Ajoute-les dans templates/")

    last_detection = 0.0

    while not exiting:
        if not running or busy:
            time.sleep(0.15)
            continue

        frame = screenshot(region)
        key, score = match_key(frame, templates)

        # Debounce: don't repeatedly detect the same UI frame.
        now = time.monotonic()
        if key and now - last_detection > DELAY_AFTER_DETECTION + 0.5:
            last_detection = now
            busy = True
            notify("🎣 Touche détectée", f"{key} — score {score:.2f}. Attente de 5 secondes…")

            for remaining in range(5, 0, -1):
                if not running or exiting:
                    break
                time.sleep(1)

            if running and not exiting:
                keyboard_controller.press(key.lower())
                keyboard_controller.release(key.lower())
                notify("🎣 Pêche", f"Touche {key} envoyée")
                wait_until_prompt_disappears(region, templates)

            busy = False

        time.sleep(SCAN_INTERVAL)


def on_press(key):
    global running, exiting
    try:
        if key == keyboard.Key.f8:
            running = not running
            notify("Noxo Fishing Bot", "🟢 Activé" if running else "🔴 Désactivé")
        elif key == keyboard.Key.f10:
            exiting = True
            running = False
            notify("Noxo Fishing Bot", "Arrêt")
            return False
    except Exception as exc:
        print("Keyboard hook error:", exc)


def main():
    print("Noxo Fishing Bot")
    print("F8 = activer/désactiver | F10 = quitter")
    print("Zone analysée : centre de l'écran")
    print("Action : attente 5 s puis Z/Q/S/D")

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    with keyboard.Listener(on_press=on_press) as listener:
        listener.join()


if __name__ == "__main__":
    main()
