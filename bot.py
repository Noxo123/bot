import os
import re
import time
import threading
import ctypes
from pathlib import Path

import cv2
import mss
import numpy as np
import pytesseract
from pynput import keyboard
from pynput.mouse import Controller as MouseController, Button
from plyer import notification

# =========================
# Configuration
# =========================
DELAY_AFTER_DETECTION = 0.0
SCAN_INTERVAL = 0.035
OCR_SCALE = 4
OCR_CONFIDENCE = 35
REGION_WIDTH_RATIO = 0.28
REGION_HEIGHT_RATIO = 0.28
KEY_REGION_RATIO = 0.13
ALLOWED_KEYS = "ZQSD"

# Couleurs du mini-jeu
BLUE_H_MIN = 90
BLUE_H_MAX = 140
BLUE_S_MIN = 65
BLUE_V_MIN = 45
RED_S_MIN = 100
RED_V_MIN = 80

# Suivi de l'anneau / curseur rouge
CIRCLE_MIN_RADIUS = 45
CIRCLE_MAX_RADIUS = 260
CIRCLE_TOLERANCE = 8
CLICK_COOLDOWN = 0.22
CIRCLE_TIMEOUT = 12.0
RING_BAND_RATIO = 0.22

keyboard_vk = {"Z": 0x5A, "Q": 0x51, "S": 0x53, "D": 0x44}
mouse_controller = MouseController()
sct = mss.MSS()
running = False
exiting = False
busy = False
config_lock = threading.Lock()


# =========================
# Windows SendInput
# =========================
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("u", INPUTUNION),
    ]


KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1


def send_key_windows(key):
    """Envoie une vraie entrée clavier Windows, plus fiable que pynput pour FiveM."""
    key = key.upper()
    vk = keyboard_vk.get(key)
    if vk is None:
        return False

    extra = ctypes.c_ulong(0)
    down = INPUT(
        type=INPUT_KEYBOARD,
        ki=KEYBDINPUT(vk, 0, 0, 0, ctypes.pointer(extra)),
    )
    up = INPUT(
        type=INPUT_KEYBOARD,
        ki=KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP, 0, ctypes.pointer(extra)),
    )

    sent = ctypes.windll.user32.SendInput(2, ctypes.byref(down), ctypes.sizeof(INPUT))
    return sent == 2


# =========================
# Tesseract / notifications
# =========================
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


# =========================
# Capture
# =========================
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


# =========================
# Détection de la touche centrale
# =========================
def detect_key(frame):
    """OCR uniquement autour du carré central du mini-jeu."""
    h, w = frame.shape[:2]
    rw = max(60, int(w * KEY_REGION_RATIO))
    rh = max(60, int(h * KEY_REGION_RATIO))
    x1 = max(0, (w - rw) // 2)
    y1 = max(0, (h - rh) // 2)
    roi = frame[y1:y1 + rh, x1:x1 + rw]

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=OCR_SCALE, fy=OCR_SCALE, interpolation=cv2.INTER_CUBIC)

    variants = [
        gray,
        cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
        cv2.threshold(gray, 155, 255, cv2.THRESH_BINARY)[1],
        cv2.threshold(gray, 155, 255, cv2.THRESH_BINARY_INV)[1],
    ]

    best_key = None
    best_conf = -1.0
    for image in variants:
        try:
            data = pytesseract.image_to_data(
                image,
                config="--psm 10 -c tessedit_char_whitelist=ZQSDzqsd",
                output_type=pytesseract.Output.DICT,
            )
        except Exception as exc:
            print(f"[OCR] Erreur : {exc}")
            return None

        for i, raw_text in enumerate(data.get("text", [])):
            text = re.sub(r"[^ZQSD]", "", raw_text.upper())
            if not text:
                continue
            try:
                confidence = float(data["conf"][i])
            except (ValueError, TypeError, IndexError):
                confidence = 0.0
            if confidence >= OCR_CONFIDENCE and confidence > best_conf:
                best_key = text[0]
                best_conf = confidence

    if best_key:
        print(f"[Détection] Touche {best_key} (confiance {best_conf:.0f}%)")
    return best_key


# =========================
# Détection couleur
# =========================
def color_masks(frame):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    blue = cv2.inRange(
        hsv,
        np.array([BLUE_H_MIN, BLUE_S_MIN, BLUE_V_MIN], dtype=np.uint8),
        np.array([BLUE_H_MAX, 255, 255], dtype=np.uint8),
    )
    red1 = cv2.inRange(
        hsv,
        np.array([0, RED_S_MIN, RED_V_MIN], dtype=np.uint8),
        np.array([12, 255, 255], dtype=np.uint8),
    )
    red2 = cv2.inRange(
        hsv,
        np.array([168, RED_S_MIN, RED_V_MIN], dtype=np.uint8),
        np.array([179, 255, 255], dtype=np.uint8),
    )
    red = cv2.bitwise_or(red1, red2)

    kernel = np.ones((3, 3), np.uint8)
    blue = cv2.morphologyEx(blue, cv2.MORPH_OPEN, kernel)
    blue = cv2.morphologyEx(blue, cv2.MORPH_CLOSE, kernel)
    red = cv2.morphologyEx(red, cv2.MORPH_OPEN, kernel)
    red = cv2.morphologyEx(red, cv2.MORPH_CLOSE, kernel)
    return blue, red


def find_ring_center(frame, blue_mask, red_mask):
    """Trouve le centre de l'anneau sans supposer sa position exacte."""
    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (9, 9), 2)

    circles = cv2.HoughCircles(
        gray,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=40,
        param1=110,
        param2=26,
        minRadius=max(CIRCLE_MIN_RADIUS, int(min(w, h) * 0.12)),
        maxRadius=min(CIRCLE_MAX_RADIUS, int(min(w, h) * 0.48)),
    )

    candidates = []
    if circles is not None:
        for cx, cy, r in np.round(circles[0]).astype(int):
            if 0 <= cx < w and 0 <= cy < h:
                candidates.append((cx, cy, r))

    if not candidates:
        # Repli : centre de la zone capturée, puisque le mini-jeu est centré.
        return w // 2, h // 2, int(min(w, h) * 0.30)

    # Le mini-jeu est généralement proche du centre de la capture.
    fx, fy = w / 2, h / 2
    return min(candidates, key=lambda c: abs(c[0] - fx) + abs(c[1] - fy))


def ring_points(mask, cx, cy, radius, thickness):
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return np.empty((0, 2), dtype=np.int32)
    dist = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
    keep = (dist >= radius - thickness) & (dist <= radius + thickness)
    return np.column_stack((xs[keep], ys[keep]))


def angle_deg(x, y, cx, cy):
    # 0° = droite, 90° = bas, 180° = gauche, 270° = haut.
    return (np.degrees(np.arctan2(y - cy, x - cx)) + 360.0) % 360.0


def angular_distance(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


def detect_fishing_target(frame):
    """Détecte l'anneau, la zone bleue et surtout la barre rouge mobile.

    La décision de clic est basée sur l'angle de la barre rouge par rapport au
    centre de l'anneau. La zone bleue peut donc être à n'importe quel angle.
    """
    blue_mask, red_mask = color_masks(frame)
    cx, cy, radius = find_ring_center(frame, blue_mask, red_mask)
    band = max(8, int(radius * RING_BAND_RATIO))

    blue_pts = ring_points(blue_mask, cx, cy, radius, band)
    red_pts = ring_points(red_mask, cx, cy, radius, band)
    if len(blue_pts) < 12 or len(red_pts) < 4:
        return None

    blue_angles = angle_deg(blue_pts[:, 0], blue_pts[:, 1], cx, cy)
    red_angles = angle_deg(red_pts[:, 0], red_pts[:, 1], cx, cy)

    # Histogramme angulaire de la zone bleue. On prend les angles réellement
    # occupés par le bleu, plutôt qu'une position codée en dur (5h-7h).
    bins = 72
    hist, edges = np.histogram(blue_angles, bins=bins, range=(0, 360))
    threshold = max(2, int(hist.max() * 0.12))
    blue_bins = np.where(hist >= threshold)[0]
    if len(blue_bins) == 0:
        return None

    def in_blue(angle):
        idx = int(angle // (360 / bins)) % bins
        nearby = [(idx + d) % bins for d in (-1, 0, 1)]
        return any(hist[b] >= threshold for b in nearby)

    # Médiane robuste de la barre rouge.
    red_angle = float(np.median(red_angles))
    inside = in_blue(red_angle)

    # Point de clic : au centre de la barre rouge sur l'anneau.
    click_radius = max(1, radius)
    rad = np.radians(red_angle)
    click_x = int(round(cx + np.cos(rad) * click_radius))
    click_y = int(round(cy + np.sin(rad) * click_radius))

    blue_density = float(hist[int(red_angle // (360 / bins)) % bins]) / max(1, hist.max())
    return {
        "center": (int(cx), int(cy)),
        "radius": int(radius),
        "angle": red_angle,
        "click": (click_x, click_y),
        "inside_blue": inside,
        "blue_density": blue_density,
    }


# =========================
# Clic / suivi
# =========================
def click_target(region, target):
    x, y = target["click"]
    screen_x = region["left"] + x
    screen_y = region["top"] + y
    mouse_controller.position = (screen_x, screen_y)
    mouse_controller.click(Button.left, 1)
    notify("🎯 Zone bleue", f"Clic automatique X={screen_x} Y={screen_y}")
    print(
        f"[CERCLE] clic X={screen_x} Y={screen_y} "
        f"angle={target['angle']:.1f}° r={target['radius']}"
    )


def wait_for_circle_and_click(region, timeout=CIRCLE_TIMEOUT):
    """Suit la barre rouge à chaque frame et clique dès qu'elle entre dans le bleu."""
    started = time.monotonic()
    last_click = 0.0
    seen = 0

    while running and not exiting and time.monotonic() - started < timeout:
        frame = screenshot(region)
        target = detect_fishing_target(frame)
        now = time.monotonic()

        if target:
            seen += 1
            cx, cy = target["center"]
            print(
                f"[CERCLE] centre=({cx},{cy}) r={target['radius']} "
                f"barre={target['angle']:.1f}° bleu={'OUI' if target['inside_blue'] else 'NON'}"
            )
            if target["inside_blue"] and now - last_click >= CLICK_COOLDOWN:
                click_target(region, target)
                return True
        else:
            seen = 0

        time.sleep(SCAN_INTERVAL)

    return False


def prompt_visible(frame):
    key = detect_key(frame)
    return key is not None


def wait_until_prompt_disappears(region, timeout=4):
    started = time.monotonic()
    while running and not exiting and time.monotonic() - started < timeout:
        if not prompt_visible(screenshot(region)):
            return
        time.sleep(SCAN_INTERVAL)


# =========================
# Worker
# =========================
def worker():
    global busy
    notify("Noxo Fishing Bot", "Prêt — F8 pour activer")
    last_key = None
    last_detection = 0.0

    while not exiting:
        if not running or busy:
            time.sleep(0.08)
            continue

        region = center_region()
        key = detect_key(screenshot(region))
        now = time.monotonic()

        if key and (key != last_key or now - last_detection > 1.2):
            last_key = key
            last_detection = now
            busy = True

            with config_lock:
                delay = DELAY_AFTER_DETECTION

            notify("Touche détectée", f"{key} — pression dans {delay:g} secondes")
            if delay > 0:
                end = time.monotonic() + delay
                while time.monotonic() < end:
                    if not running or exiting:
                        break
                    time.sleep(0.01)

            if running and not exiting:
                sent = send_key_windows(key)
                if sent:
                    notify("🎣 Touche pressée", f"La touche {key} a été envoyée à Windows")
                else:
                    notify("⚠️ Touche", f"Échec de l'envoi Windows pour {key}")

                clicked = wait_for_circle_and_click(region)
                if not clicked:
                    print("[CERCLE] Barre rouge / zone bleue non exploitable dans le délai.")

                wait_until_prompt_disappears(region)

            busy = False

        time.sleep(SCAN_INTERVAL)

    notify("Noxo Fishing Bot", "Arrêté")


# =========================
# Terminal
# =========================
def print_help():
    print("""
========== COMMANDES ==========
  help                 Affiche cette aide
  on / off             Active / désactive le bot
  toggle               Change l'état du bot
  status               Affiche la configuration actuelle
  delay <secondes>     Change le délai avant la touche (0 par défaut)
  interval <secondes>  Change la fréquence de scan
  confidence <0-100>   Seuil de confiance OCR
  region <largeur> <hauteur>
                       Taille de la zone centrale en % de l'écran
  tolerance <pixels>   Tolérance de suivi
  test                 Teste la détection OCR centrale
  testcircle           Teste anneau + bleu + barre rouge
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
            f"touche-centre={KEY_REGION_RATIO * 100:g}% | "
            f"anneau={CIRCLE_MIN_RADIUS}-{CIRCLE_MAX_RADIUS}px | "
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
                print(f"[CONFIG] Tolérance = {value:g}px")
            elif cmd == "test":
                key = detect_key(screenshot(center_region()))
                print(f"[TEST OCR] Lettre détectée : {key or 'AUCUNE'}")
            elif cmd == "testcircle":
                region = center_region()
                target = detect_fishing_target(screenshot(region))
                if target:
                    print(
                        f"[TEST CERCLE] centre={target['center']} r={target['radius']} "
                        f"barre={target['angle']:.1f}° bleu={'OUI' if target['inside_blue'] else 'NON'} "
                        f"clic={target['click']}"
                    )
                else:
                    print("[TEST CERCLE] Anneau, bleu ou barre rouge non détecté.")
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


# =========================
# Hotkeys / main
# =========================
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
    print("Noxo Fishing Bot — touche centrale + suivi barre rouge / zone bleue")
    print("F8 = activer/desactiver | F10 = quitter")

    if os.name != "nt":
        print("[ERREUR] Cette version utilise Windows SendInput et nécessite Windows.")
        return

    if not setup_tesseract():
        print("Installe Tesseract ou vérifie son chemin avant de lancer la détection.")
        return

    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=terminal_worker, daemon=True).start()
    with keyboard.Listener(on_press=on_press) as listener:
        listener.join()


if __name__ == "__main__":
    main()
