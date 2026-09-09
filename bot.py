import time
import threading

import cv2
import mss
import numpy as np
import pytesseract
from pynput import keyboard
from pynput.keyboard import Controller
from plyer import notification

DELAY_AFTER_DETECTION = 5.0
SCAN_INTERVAL = 0.10
OCR_SCALE = 3

keyboard_controller = Controller()
sct = mss.MSS()
running = False
exiting = False
busy = False


def notify(title, message):
    """Envoie une notification Windows et garde un fallback console."""
    print(f"[{title}] {message}")
    try:
        notification.notify(
            title=title,
            message=message,
            app_name="Noxo Fishing Bot",
            timeout=3,
        )
    except Exception as exc:
        print(f"Notification Windows indisponible: {exc}")


def center_region():
    monitor = sct.monitors[1]
    width, height = monitor["width"], monitor["height"]
    region_w, region_h = int(width * 0.28), int(height * 0.22)
    return {
        "left": monitor["left"] + (width - region_w) // 2,
        "top": monitor["top"] + (height - region_h) // 2,
        "width": region_w,
        "height": region_h,
    }


def screenshot(region):
    frame = np.array(sct.grab(region))
    return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)


def detect_key(frame):
    """OCR limité strictement aux quatre touches possibles : Z, Q, S, D."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=OCR_SCALE, fy=OCR_SCALE, interpolation=cv2.INTER_CUBIC)
    variants = [
        gray,
        cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
        cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)[1],
        cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)[1],
    ]
    config = "--psm 10 -c tessedit_char_whitelist=ZQSDzqsd"

    for image in variants:
        text = pytesseract.image_to_string(image, config=config).upper()
        for char in text:
            if char in "ZQSD":
                return char
    return None


def prompt_visible(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    ratio = cv2.countNonZero(binary) / binary.size
    return 0.001 < ratio < 0.50


def wait_until_prompt_disappears(region, timeout=15):
    started = time.monotonic()
    while running and not exiting and time.monotonic() - started < timeout:
        if not prompt_visible(screenshot(region)):
            return
        time.sleep(SCAN_INTERVAL)


def worker():
    global busy
    region = center_region()
    notify("Noxo Fishing Bot", "Prêt — F8 pour activer")
    last_key = None
    last_detection = 0.0

    while not exiting:
        if not running or busy:
            time.sleep(0.15)
            continue

        key = detect_key(screenshot(region))
        now = time.monotonic()

        if key and (key != last_key or now - last_detection > DELAY_AFTER_DETECTION + 1):
            last_key = key
            last_detection = now
            busy = True

            notify("Touche détectée", f"{key} — pression dans 5 secondes")

            for _ in range(5):
                if not running or exiting:
                    break
                time.sleep(1)

            if running and not exiting:
                key_to_press = key.lower()
                keyboard_controller.press(key_to_press)
                keyboard_controller.release(key_to_press)

                # Notification envoyée exactement après l'appui sur la touche.
                notify("🎣 Touche pressée", f"La touche {key} a été envoyée")

                wait_until_prompt_disappears(region)

            busy = False

    notify("Noxo Fishing Bot", "Arrêté")


def on_press(key):
    global running, exiting
    try:
        if key == keyboard.Key.f8:
            running = not running
            notify("Noxo Fishing Bot", "Activé" if running else "Désactivé")
        elif key == keyboard.Key.f10:
            exiting = True
            running = False
            notify("Noxo Fishing Bot", "Arrêt")
            return False
    except Exception as exc:
        print("Keyboard hook error:", exc)


def main():
    print("Noxo Fishing Bot - OCR Z/Q/S/D")
    print("F8 = activer/desactiver | F10 = quitter")
    print("Détection directe de la lettre au centre de l'écran")
    print("Action : attente 5 s puis pression de Z/Q/S/D")
    print("Une notification est envoyée après chaque touche pressée.")
    print("Tesseract OCR doit être installé et accessible dans le PATH.")

    threading.Thread(target=worker, daemon=True).start()
    with keyboard.Listener(on_press=on_press) as listener:
        listener.join()


if __name__ == "__main__":
    main()
