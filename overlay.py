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
        self.root.geometry("360x300+40+40")
        self.root.minsize(320, 260)
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

        self.build_ui()
        self.start_bot()
        self.root.after(250, self.refresh_ui)

    def build_ui(self):
        header = tk.Frame(self.root, bg=PANEL, height=42)
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
        body.pack(fill="both", expand=True, padx=12, pady=10)

        self.status_dot = tk.Label(body, text="●", bg=BG, fg=RED, font=("Segoe UI", 14))
        self.status_dot.grid(row=0, column=0, sticky="w")
        self.status_label = tk.Label(body, text="DÉSACTIVÉ", bg=BG, fg=TEXT,
                                     font=("Segoe UI", 11, "bold"))
        self.status_label.grid(row=0, column=1, sticky="w")

        self.key_label = self.info_row(body, 1, "Touche détectée")
        self.circle_label = self.info_row(body, 2, "Cercle")
        self.action_label = self.info_row(body, 3, "Dernière action")

        buttons = tk.Frame(body, bg=BG)
        buttons.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        buttons.columnconfigure((0, 1, 2), weight=1)

        self.make_button(buttons, "ACTIVER", "on", 0, GREEN)
        self.make_button(buttons, "DÉSACTIVER", "off", 1, RED)
        self.make_button(buttons, "TOGGLE", "toggle", 2, BLUE)

        hint = tk.Label(body, text="F8 = activer/désactiver  •  F10 = fermer\nGlisse la barre du haut pour déplacer l'overlay",
                        bg=BG, fg=MUTED, font=("Segoe UI", 8), justify="center")
        hint.grid(row=5, column=0, columnspan=2, pady=(14, 0))

    def info_row(self, parent, row, name):
        tk.Label(parent, text=name, bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).grid(row=row, column=0, sticky="w", pady=4)
        label = tk.Label(parent, text="—", bg=BG, fg=TEXT,
                         font=("Segoe UI", 9, "bold"))
        label.grid(row=row, column=1, sticky="e", pady=4)
        parent.columnconfigure(1, weight=1)
        return label

    def make_button(self, parent, text, command, col, color):
        tk.Button(parent, text=text, command=lambda: self.send_command(command),
                  bg=PANEL, fg=color, activebackground="#232a37", activeforeground=color,
                  bd=0, relief="flat", font=("Segoe UI", 8, "bold"), pady=7).grid(
                      row=0, column=col, padx=3, sticky="ew")

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
            self.last_action = f"Erreur: {exc}"

    def parse_line(self, line):
        upper = line.upper()
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
                self.last_action = f"Touche {self.last_key} détectée"
        elif "[CERCLE] POSITION DYNAMIQUE" in upper:
            match = re.search(r"x=(\d+)\s+y=(\d+)\s+r=(\d+)", line)
            if match:
                self.last_circle = f"X {match.group(1)}  Y {match.group(2)}  R {match.group(3)}"
        elif "[CERCLE] CLIC" in upper:
            self.last_action = "Clic automatique"
        elif "TOUCHE PRESSÉE" in upper:
            self.last_action = "Touche envoyée"
        elif "AUCUN CERCLE" in upper:
            self.last_action = "Cercle non détecté"
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
            self.last_action = f"Commande impossible: {exc}"

    def refresh_ui(self):
        active = self.running
        self.status_dot.config(fg=GREEN if active else RED)
        self.status_label.config(text="ACTIVÉ" if active else "DÉSACTIVÉ")
        self.key_label.config(text=self.last_key)
        self.circle_label.config(text=self.last_circle)
        self.action_label.config(text=self.last_action)
        self.root.after(250, self.refresh_ui)

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
