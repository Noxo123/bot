import os
import time
import threading
from pathlib import Path

import cv2
import mss
import numpy as np
import pytesseract
from pynput import keyboard
from pynput.keyboard import Controller
from plyer import notification

# =========================
# Configuration
# =========================
DELAY_AFTER_DETECTION = 5.0
SCAN_INTERVAL = 0.10
OCR_SCALE = 3
OCR_CONFIDENCE = 45
REGION_WIDTH_RATIO = 0.28
REGION_HEIGHT_RATIO = 0.22
ALLOWED_KEYS = "ZQSD"

keyboard_controller = Controller()
sct = mss.MSS()
running = False
exiting = False
busy = False
config_lock = threading.Lock()


def setup_tesseract():
    """Trouve automatiquement tesseract.exe sur Windows."""
    found = []

    try:
        import shutil
        path = shutil.which("tesseract")
        if path:
            found.append(path)
    except Exception:
        pass

    found.extend([
        r"C:\\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
    ])

    for candidate in found:
        if candidate and Path(candidate).is_file():
            pytesseract.pytesseract.tesseract_cmd = candidate
            print(f"[Tesseract] Utilisé : {candidate}")
            try:
                version = pytesseract.get_tesseract_version()
                print(f"[Tesseract] Version : {version}")
            except Exception as exc:
                print(f"[Tesseract] Trouvé mais test impossible : {exc}")
            return True

    print("[ERREUR] tesseract.exe introuvable.")
    print(r"        Chemin attendu : C:\Program Files\Tesseract-OCR\tesseract.exe")
    return False


def notify(title, message):
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
    with config_lock:
        region_w = int(width * REGION_WIDTH_RATIO)
        region_h = int(height * REGION_HEIGHT_RATIO)
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
    """Détecte automatiquement Z/Q/S/D avec OCR + score de confiance."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=OCR_SCALE, fy=OCR_SCALE, interpolation=cv2.INTER_CUBIC)

    variants = [
        gray,
        cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
        cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)[1],
        cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)[1],
    ]

    config = "--psm 11 -c tessedit_char_whitelist=ZQSDzqsd"
    best_key = None
    best_conf = -1.0

    for image in variants:
        try:
            data = pytesseract.image_to_data(
                image,
                config=config,
                output_type=pytesseract.Output.DICT,
            )
        except Exception as exc:
            print(f"[OCR] Erreur : {exc}")
            return None

        for i, raw_text in enumerate(data.get("text", [])):
            text = raw_text.strip().upper()
            if not text:
                continue

            try:
                confidence = float(data["conf"][i])
            except (ValueError, TypeError, IndexError):
                confidence = 0.0

            # Le texte peut contenir plusieurs caractères/bruits OCR.
            for char in text:
                if char in ALLOWED_KEYS and confidence >= OCR_CONFIDENCE:
                    if confidence > best_conf:
                        best_key = char
                        best_conf = confidence

    if best_key:
        print(f"[Détection] Touche {best_key} (confiance {best_conf:.0f}%)")
    return best_key


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
    notify("Noxo Fishing Bot", "Prêt — F8 pour activer")
    last_key = None
    last_detection = 0.0

    while not exiting:
        if not running or busy:
            time.sleep(0.15)
            continue

        region = center_region()
        key = detect_key(screenshot(region))
        now = time.monotonic()

        if key and (key != last_key or now - last_detection > DELAY_AFTER_DETECTION + 1):
            last_key = key
            last_detection = now
            busy = True

            with config_lock:
                delay = DELAY_AFTER_DETECTION

            notify("Touche détectée", f"{key} — pression dans {delay:g} secondes")

            end = time.monotonic() + delay
            while time.monotonic() < end:
                if not running or exiting:
                    break
                time.sleep(0.05)

            if running and not exiting:
                key_to_press = key.lower()
                keyboard_controller.press(key_to_press)
                keyboard_controller.release(key_to_press)
                notify("🎣 Touche pressée", f"La touche {key} a été envoyée")
                wait_until_prompt_disappears(region)

            busy = False

        time.sleep(SCAN_INTERVAL)

    notify("Noxo Fishing Bot", "Arrêté")


def print_help():
    print("""
========== COMMANDES ==========
  help                 Affiche cette aide
  on / off             Active / désactive le bot
  toggle               Change l'état du bot
  status               Affiche la configuration actuelle
  delay <secondes>     Change le délai avant la touche
  interval <secondes>  Change la fréquence de scan
  confidence <0-100>   Seuil de confiance OCR
  region <largeur> <hauteur>
                       Taille de la zone centrale en % de l'écran
  test                 Teste la détection immédiatement
  notify               Teste une notification Windows
  quit / exit          Ferme le bot
  ===============================
  F8                   Active / désactive
  F10                  Ferme le bot
================================
""")


def print_status():
    with config_lock:
        print(
            f"[STATUS] {'ACTIVÉ' if running else 'DÉSACTIVÉ'} | "
            f"délai={DELAY_AFTER_DETECTION:g}s | scan={SCAN_INTERVAL:g}s | "
            f"confiance={OCR_CONFIDENCE:g}% | "
            f"zone={REGION_WIDTH_RATIO * 100:g}% x {REGION_HEIGHT_RATIO * 100:g}% | "
            f"touches={ALLOWED_KEYS}"
        )


def terminal_worker():
    global running, exiting
    print_help()
    while not exiting:
        try:
            command = input("Noxo> ").strip()
        except (EOFError, KeyboardInterrupt):
            exiting = True
            running = False
            break

        if not command:
            continue

        parts = command.split()
        cmd = parts[0].lower()

        try:
            if cmd == "help":
                print_help()
            elif cmd == "on":
                running = True
                notify("Noxo Fishing Bot", "Activé")
            elif cmd == "off":
                running = False
                notify("Noxo Fishing Bot", "Désactivé")
            elif cmd == "toggle":
                running = not running
                notify("Noxo Fishing Bot", "Activé" if running else "Désactivé")
            elif cmd == "status":
                print_status()
            elif cmd == "delay" and len(parts) == 2:
                value = float(parts[1])
                if value < 0:
                    raise ValueError
                with config_lock:
                    globals()["DELAY_AFTER_DETECTION"] = value
                print(f"[CONFIG] Délai = {value:g}s")
            elif cmd == "interval" and len(parts) == 2:
                value = float(parts[1])
                if value <= 0:
                    raise ValueError
                with config_lock:
                    globals()["SCAN_INTERVAL"] = value
                print(f"[CONFIG] Intervalle = {value:g}s")
            elif cmd == "confidence" and len(parts) == 2:
                value = float(parts[1])
                if not 0 <= value <= 100:
                    raise ValueError
                with config_lock:
                    globals()["OCR_CONFIDENCE"] = value
                print(f"[CONFIG] Confiance OCR = {value:g}%")
            elif cmd == "region" and len(parts) == 3:
                width = float(parts[1])
                height = float(parts[2])
                if not 1 <= width <= 100 or not 1 <= height <= 100:
                    raise ValueError
                with config_lock:
                    globals()["REGION_WIDTH_RATIO"] = width / 100
                    globals()["REGION_HEIGHT_RATIO"] = height / 100
                print(f"[CONFIG] Zone centrale = {width:g}% x {height:g}%")
            elif cmd == "test":
                key = detect_key(screenshot(center_region()))
                print(f"[TEST] Lettre détectée : {key or 'AUCUNE'}")
            elif cmd == "notify":
                notify("Noxo Fishing Bot", "Notification de test OK")
            elif cmd in ("quit", "exit"):
                exiting = True
                running = False
                notify("Noxo Fishing Bot", "Arrêt")
            else:
                print("Commande inconnue. Tape 'help'.")
        except (ValueError, IndexError):
            print("Valeur invalide. Tape 'help' pour voir la syntaxe.")


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
    print("Noxo Fishing Bot — détection automatique Z/Q/S/D")
    print("F8 = activer/desactiver | F10 = quitter")

    if not setup_tesseract():
        print("Installe Tesseract ou vérifie son chemin avant de lancer la détection.")
        return

    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=terminal_worker, daemon=True).start()

    with keyboard.Listener(on_press=on_press) as listener:
        listener.join()


if __name__ == "__main__":
    main()
