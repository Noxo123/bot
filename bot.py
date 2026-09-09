import os
import time
import threading
from pathlib import Path

import cv2
import mss
import numpy as np
import pytesseract
from pynput import keyboard, mouse
from pynput.keyboard import Controller as KeyboardController
from pynput.mouse import Controller as MouseController, Button
from plyer import notification

# =========================
# Configuration
# =========================
DELAY_AFTER_DETECTION = 5.0
SCAN_INTERVAL = 0.06
OCR_SCALE = 3
OCR_CONFIDENCE = 45
REGION_WIDTH_RATIO = 0.28
REGION_HEIGHT_RATIO = 0.22
ALLOWED_KEYS = "ZQSD"

# Detection dynamique du mini-jeu cercle/bleu.
BLUE_H_MIN = 90
BLUE_H_MAX = 140
BLUE_S_MIN = 70
BLUE_V_MIN = 45
CIRCLE_MIN_RADIUS = 5
CIRCLE_MAX_RADIUS = 100
CIRCLE_TOLERANCE = 3
CLICK_COOLDOWN = 0.30
CIRCLE_REQUIRED_FRAMES = 2

keyboard_controller = KeyboardController()
mouse_controller = MouseController()
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
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
    ])

    for candidate in found:
        if candidate and Path(candidate).is_file():
            pytesseract.pytesseract.tesseract_cmd = candidate
            print(f"[Tesseract] Utilisé : {candidate}")
            try:
                print(f"[Tesseract] Version : {pytesseract.get_tesseract_version()}")
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
                image, config=config, output_type=pytesseract.Output.DICT
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
            for char in text:
                if char in ALLOWED_KEYS and confidence >= OCR_CONFIDENCE and confidence > best_conf:
                    best_key = char
                    best_conf = confidence

    if best_key:
        print(f"[Détection] Touche {best_key} (confiance {best_conf:.0f}%)")
    return best_key


def detect_blue_mask(frame):
    """Construit un masque HSV pour retrouver la zone bleue, quelle que soit sa position."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lower = np.array([BLUE_H_MIN, BLUE_S_MIN, BLUE_V_MIN], dtype=np.uint8)
    upper = np.array([BLUE_H_MAX, 255, 255], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower, upper)
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return mask


def detect_circle_in_blue(frame):
    """Cherche le cercle à sa position actuelle et renvoie (x, y, r, score).

    La position n'est jamais fixe : chaque frame est analysée. On privilégie
    les cercles dont le centre tombe dans la zone bleue et dont le contour
    contraste avec le bleu.
    """
    mask = detect_blue_mask(frame)
    blue_contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not blue_contours:
        return None

    # On garde les zones bleues suffisamment grandes pour être un élément du mini-jeu.
    candidates_blue = [c for c in blue_contours if cv2.contourArea(c) >= 80]
    if not candidates_blue:
        return None
    blue_contour = max(candidates_blue, key=cv2.contourArea)
    bx, by, bw, bh = cv2.boundingRect(blue_contour)

    roi = frame[by:by + bh, bx:bx + bw]
    if roi.size == 0:
        return None

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (7, 7), 1.5)
    circles = cv2.HoughCircles(
        gray,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=max(8, CIRCLE_MIN_RADIUS * 2),
        param1=90,
        param2=16,
        minRadius=CIRCLE_MIN_RADIUS,
        maxRadius=min(CIRCLE_MAX_RADIUS, max(CIRCLE_MIN_RADIUS + 1, min(bw, bh) // 2)),
    )

    best = None
    best_score = -1e9
    blue_mask_roi = mask[by:by + bh, bx:bx + bw]

    if circles is None:
        return None

    for cx, cy, radius in np.round(circles[0]).astype(int):
        if not (0 <= cx < bw and 0 <= cy < bh):
            continue
        global_x, global_y = bx + cx, by + cy
        inside_blue = blue_mask_roi[cy, cx] > 0
        if not inside_blue:
            continue

        # Mesure le contraste sur l'anneau du cercle : utile pour éviter de
        # sélectionner un simple reflet ou une petite tache bleue.
        yy, xx = np.ogrid[:bh, :bw]
        dist2 = (xx - cx) ** 2 + (yy - cy) ** 2
        ring = (dist2 >= max(1, (radius - 2) ** 2)) & (dist2 <= (radius + 2) ** 2)
        ring_pixels = gray[ring]
        contrast = float(np.std(ring_pixels)) if ring_pixels.size else 0.0

        score = (contrast * 2.0) + min(radius, 30) - (abs(cx - bw / 2) + abs(cy - bh / 2)) * 0.01
        if score > best_score:
            best_score = score
            best = (global_x, global_y, int(radius), score)

    return best


def click_circle(region, circle):
    """Clique aux coordonnées écran correspondant au cercle détecté."""
    x, y, radius, score = circle
    screen_x = region["left"] + x
    screen_y = region["top"] + y
    mouse_controller.position = (screen_x, screen_y)
    mouse_controller.click(Button.left, 1)
    notify("🎯 Cercle détecté", f"Clic automatique : X={screen_x} Y={screen_y}")
    print(f"[CERCLE] clic X={screen_x} Y={screen_y} r={radius} score={score:.1f}")


def wait_for_circle_and_click(region, timeout=15):
    """Suit le cercle frame par frame et clique dès qu'il est dans le bleu."""
    started = time.monotonic()
    consecutive = 0
    last_click = 0.0

    while running and not exiting and time.monotonic() - started < timeout:
        frame = screenshot(region)
        circle = detect_circle_in_blue(frame)
        now = time.monotonic()

        if circle:
            consecutive += 1
            print(f"[CERCLE] position dynamique x={circle[0]} y={circle[1]} r={circle[2]}")
            if consecutive >= CIRCLE_REQUIRED_FRAMES and now - last_click >= CLICK_COOLDOWN:
                click_circle(region, circle)
                last_click = now
                return True
        else:
            consecutive = 0

        time.sleep(SCAN_INTERVAL)

    return False


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

                # Après la touche, le mini-jeu peut déplacer le cercle à chaque frame.
                # On le suit dynamiquement et on clique lorsqu'il est dans le bleu.
                clicked = wait_for_circle_and_click(region)
                if not clicked:
                    print("[CERCLE] Aucun cercle exploitable détecté dans le délai.")

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
  tolerance <pixels>   Tolérance/paramètre de suivi du cercle
  test                 Teste la détection OCR
  testcircle           Teste le cercle bleu et affiche sa position
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
            f"cercle rayon={CIRCLE_MIN_RADIUS}-{CIRCLE_MAX_RADIUS}px | "
            f"tolérance={CIRCLE_TOLERANCE}px | touches={ALLOWED_KEYS}"
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
            elif cmd == "tolerance" and len(parts) == 2:
                value = float(parts[1])
                if value < 0:
                    raise ValueError
                with config_lock:
                    globals()["CIRCLE_TOLERANCE"] = value
                print(f"[CONFIG] Tolérance cercle = {value:g}px")
            elif cmd == "test":
                key = detect_key(screenshot(center_region()))
                print(f"[TEST OCR] Lettre détectée : {key or 'AUCUNE'}")
            elif cmd == "testcircle":
                region = center_region()
                circle = detect_circle_in_blue(screenshot(region))
                if circle:
                    print(
                        f"[TEST CERCLE] x={circle[0]} y={circle[1]} "
                        f"r={circle[2]} score={circle[3]:.1f} | "
                        f"écran=({region['left'] + circle[0]}, {region['top'] + circle[1]})"
                    )
                else:
                    print("[TEST CERCLE] Aucun cercle dans le bleu détecté.")
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
    print("Noxo Fishing Bot — OCR Z/Q/S/D + suivi dynamique du cercle bleu")
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
