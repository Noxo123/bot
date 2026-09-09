import os
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_PATH = os.path.join(BASE_DIR, "bot.py")

BG = "#10131a"
PANEL = "#181d27"
TEXT = "#f2f4f8"
MUTED = "#8d96a8"
GREEN = "#45d483"
RED = "#ff5d6c"
BLUE = "#5aa7ff"


class FishingOverlay:
    def __init__(self, root):
        self.root = root
        self.root.title("Noxo Fishing Bot")
        self.root.geometry("430x390+40+40")
        self.root.minsize(390, 350)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.process = None
        self.running = False
        self.drag_x = 0
        self.drag_y = 0
        self.last_key = "—"
        self.last_circle = "—"
        self.last_action = "En attente"
        self.bar_angle = "—"
        self.blue_state = "—"
        self.click_count = 0
        self.key_count = 0
        self.last_error = "—"

        self.build_ui()
        self.start_bot()
        self.root.after(200, self.refresh_ui)

    def build_ui(self):
        header = tk.Frame(self.root, bg=PANEL, height=44)
        header.pack(fill="x")
        header.pack_propagate(False)
        header.bind("<ButtonPress-1>", self.start_drag)
        header.bind("<B1-Motion>", self.drag)

        title = tk.Label(header, text="🎣  NOXO FISHING", bg=PANEL, fg=TEXT,
                         font=("Segoe UI", 11, "bold"))
        title.pack(side="left", padx=12)
        title.bind("<ButtonPress-1>", self.start_drag)
        title.bind("<B1-Motion>", self.drag)

        close = tk.Button(header, text="×", command=self.close, bg=PANEL, fg=MUTED,
                          activebackground=PANEL, activeforeground=TEXT, bd=0,
                          font=("Segoe UI", 16), width=2)
        close.pack(side="right", padx=4)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=10)

        self.status_dot = tk.Label(body, text="●", bg=BG, fg=RED, font=("Segoe UI", 14))
        self.status_dot.grid(row=0, column=0, sticky="w")
        self.status_label = tk.Label(body, text="DÉSACTIVÉ", bg=BG, fg=TEXT,
                                     font=("Segoe UI", 11, "bold"))
        self.status_label.grid(row=0, column=1, sticky="w")

        self.key_label = self.info_row(body, 1, "Touche détectée")
        self.bar_label = self.info_row(body, 2, "Barre rouge")
        self.blue_label = self.info_row(body, 3, "Zone bleue")
        self.circle_label = self.info_row(body, 4, "Anneau")
        self.action_label = self.info_row(body, 5, "Dernière action")
        self.stats_label = self.info_row(body, 6, "Statistiques")
        self.error_label = self.info_row(body, 7, "Erreur")

        buttons = tk.Frame(body, bg=BG)
        buttons.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        for col in range(3):
            buttons.columnconfigure(col, weight=1)

        self.make_button(buttons, "ACTIVER", "on", 0, GREEN)
        self.make_button(buttons, "DÉSACTIVER", "off", 1, RED)
        self.make_button(buttons, "TOGGLE", "toggle", 2, BLUE)

        hint = tk.Label(
            body,
            text="F8 = activer/désactiver  •  F10 = fermer\nGlisse la barre du haut pour déplacer l'overlay",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 8),
            justify="center",
        )
        hint.grid(row=9, column=0, columnspan=2, pady=(10, 0))

    def info_row(self, parent, row, name):
        tk.Label(parent, text=name, bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).grid(row=row, column=0, sticky="w", pady=3)
        label = tk.Label(parent, text="—", bg=BG, fg=TEXT,
                         font=("Segoe UI", 9, "bold"))
        label.grid(row=row, column=1, sticky="e", pady=3)
        parent.columnconfigure(1, weight=1)
        return label

    def make_button(self, parent, text, command, col, color):
        tk.Button(
            parent,
            text=text,
            command=lambda: self.send_command(command),
            bg=PANEL,
            fg=color,
            activebackground="#232a37",
            activeforeground=color,
            bd=0,
            relief="flat",
            font=("Segoe UI", 8, "bold"),
            pady=7,
        ).grid(row=0, column=col, padx=3, sticky="ew")

    def start_drag(self, event):
        self.drag_x = event.x_root - self.root.winfo_x()
        self.drag_y = event.y_root - self.root.winfo_y()

    def drag(self, event):
        x = event.x_root - self.drag_x
        y = event.y_root - self.drag_y
        self.root.geometry(f"+{x}+{y}")

    def start_bot(self):
        if not os.path.isfile(BOT_PATH):
            messagebox.showerror("Noxo Fishing Bot", "bot.py est introuvable.")
            return

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-u", BOT_PATH],
                cwd=BASE_DIR,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=creationflags,
            )
            threading.Thread(target=self.read_output, daemon=True).start()
            self.send_command("status")
        except Exception as exc:
            messagebox.showerror("Noxo Fishing Bot", f"Impossible de lancer bot.py :\n{exc}")

    def read_output(self):
        if not self.process or not self.process.stdout:
            return
        try:
            for raw in self.process.stdout:
                line = raw.strip()
                if line:
                    self.parse_line(line)
        except Exception as exc:
            self.last_error = str(exc)
            self.last_action = "Erreur de lecture"

    def parse_line(self, line):
        upper = line.upper()
        if "[ERREUR]" in upper or "ÉCHEC" in upper:
            self.last_error = line
        if "ACTIVÉ" in upper:
            self.running = True
            self.last_action = "Bot activé"
        elif "DÉSACTIVÉ" in upper:
            self.running = False
            self.last_action = "Bot désactivé"
        elif "[DÉTECTION] TOUCHE" in upper:
            match = re.search(r"TOUCHE\s+([ZQSD])", upper)
            if match:
                self.last_key = match.group(1)
                self.key_count += 1
                self.last_action = f"Touche {self.last_key} détectée"
        elif "[CERCLE] CENTRE=" in upper:
            match = re.search(r"CENTRE=\((\d+),(\d+)\)\s+r=(\d+)\s+BARRE=([0-9.]+)°\s+BLEU=(OUI|NON)", upper)
            if match:
                self.last_circle = f"X {match.group(1)}  Y {match.group(2)}  R {match.group(3)}"
                self.bar_angle = f"{match.group(4)}°"
                self.blue_state = match.group(5)
        elif "[CERCLE] CLIC" in upper:
            self.click_count += 1
            self.last_action = "Clic automatique sur le bleu"
        elif "TOUCHE PRESSÉE" in upper:
            self.last_action = "Touche envoyée à Windows"
        elif "BARRE ROUGE / ZONE BLEUE NON EXPLOITABLE" in upper:
            self.last_action = "Cible non détectée"
        elif "ARRÊTÉ" in upper:
            self.running = False
            self.last_action = "Bot arrêté"

    def send_command(self, command):
        if not self.process or self.process.poll() is not None or not self.process.stdin:
            self.last_action = "Bot non lancé"
            return
        try:
            self.process.stdin.write(command + "\n")
            self.process.stdin.flush()
        except Exception as exc:
            self.last_error = str(exc)
            self.last_action = "Commande impossible"

    def refresh_ui(self):
        active = self.running
        self.status_dot.config(fg=GREEN if active else RED)
        self.status_label.config(text="ACTIVÉ" if active else "DÉSACTIVÉ")
        self.key_label.config(text=self.last_key)
        self.bar_label.config(text=self.bar_angle)
        self.blue_label.config(text=self.blue_state)
        self.circle_label.config(text=self.last_circle)
        self.action_label.config(text=self.last_action)
        self.stats_label.config(text=f"Touches {self.key_count}  •  Clics {self.click_count}")
        self.error_label.config(text=self.last_error)
        self.root.after(200, self.refresh_ui)

    def close(self):
        if self.process and self.process.poll() is None:
            try:
                self.send_command("quit")
                self.process.wait(timeout=2)
            except Exception:
                try:
                    self.process.terminate()
                except Exception:
                    pass
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = FishingOverlay(root)
    root.mainloop()
