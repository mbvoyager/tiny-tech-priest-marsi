"""Lightweight Tk display + push-to-talk interface for the Raspberry Pi 3 B+."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime
import io
import math
import os
import queue
import subprocess
import tempfile
import threading
import time
import wave

from .ambient import Ambient
from .client import Client, ClientError
from .config import load_env
from .core import ritual


def record(stop: threading.Event, seconds=15) -> bytes:
    # RawInputStream needs no NumPy and keeps audio in memory only.
    import sounddevice as sd
    rate = int(os.getenv("MARSI_MIC_RATE", "16000"))
    if rate not in (16000, 22050, 44100, 48000):
        raise ValueError("MARSI_MIC_RATE must be 16000, 22050, 44100 or 48000")
    device = os.getenv("MARSI_MIC_DEVICE") or None
    if device and device.isdecimal():
        device = int(device)
    chunks, sample_bytes = [], 0

    def callback(data, frames, timing, status):
        nonlocal sample_bytes
        if not stop.is_set() and sample_bytes < rate * seconds * 2:
            block = bytes(data)[:rate * seconds * 2 - sample_bytes]
            chunks.append(block)
            sample_bytes += len(block)
        if sample_bytes >= rate * seconds * 2:
            stop.set()

    with sd.RawInputStream(samplerate=rate, channels=1, dtype="int16", device=device, callback=callback):
        stop.wait(seconds)
        stop.set()
    if sample_bytes < rate // 5:
        raise ValueError("Recording was too short. Press Talk, speak, then press Finish.")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"".join(chunks))
    return output.getvalue()


def play(audio: bytes, on_process=lambda process: None):
    # aplay receives only a local filename, never model-generated command text.
    device = os.getenv("MARSI_SPEAKER_DEVICE")
    with tempfile.TemporaryDirectory(prefix="marsi-") as folder:
        path = os.path.join(folder, "reply.wav")
        with open(path, "wb") as file:
            file.write(audio)
        command = ["aplay", "-q"] + (["-D", device] if device else []) + [path]
        with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as process:
            on_process(process)
            try:
                if process.wait(timeout=120) != 0:
                    raise subprocess.CalledProcessError(process.returncode, command)
            except subprocess.TimeoutExpired:
                process.kill()
                raise
            finally:
                on_process(None)


class Display:
    def __init__(self, root, client: Client, windowed=False):
        import tkinter as tk
        self.tk, self.root, self.client = tk, root, client
        self.events = queue.Queue()
        self.busy, self.recording, self.closed = False, False, False
        self.stop = threading.Event()
        self.mode, self.animation = "idle", "happy"
        self.animation_until = 0.0
        self.last_interaction = time.monotonic()
        self.playback = None
        self.ambient = Ambient(
            minimum=float(os.getenv("MARSI_RITUAL_MIN_SECONDS", "600")),
            maximum=float(os.getenv("MARSI_RITUAL_MAX_SECONDS", "1200")),
            quiet_start=int(os.getenv("MARSI_QUIET_START", "22")),
            quiet_end=int(os.getenv("MARSI_QUIET_END", "8")),
        )
        self.speak = tk.BooleanVar(value=os.getenv("MARSI_SPEAK", "true").lower() == "true")
        self.rituals = tk.BooleanVar(value=os.getenv("MARSI_RITUALS", "true").lower() == "true")
        self.ritual_speech = os.getenv("MARSI_RITUAL_SPEECH", "false").lower() == "true"
        root.title("Marsi's tiny forge")
        root.geometry("800x480")
        root.minsize(480, 320)
        root.configure(bg="#17151c")
        root.attributes("-fullscreen", not windowed)
        root.bind("<Escape>", lambda event: root.attributes("-fullscreen", False))
        root.bind("<Control-q>", lambda event: self.close())
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=3)
        root.rowconfigure(2, weight=1)
        self.status = tk.StringVar(value="Tiny forge ready · tap Talk or type a message")
        tk.Label(root, textvariable=self.status, bg="#17151c", fg="#dcc5a1", font=("sans", 11), pady=5).grid(row=0, column=0, sticky="ew")
        self.canvas = tk.Canvas(root, bg="#17151c", highlightthickness=0, height=200)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.reply = tk.Text(root, height=3, bg="#24212b", fg="#fff0d4", font=("sans", 12),
                             wrap="word", relief="flat", padx=12, pady=5)
        self.reply.grid(row=2, column=0, sticky="nsew", padx=8)
        self.show_text("Beep boop. I am Marsi. Your tiny forge companion is ready.")
        controls = tk.Frame(root, bg="#17151c")
        controls.grid(row=3, column=0, sticky="ew", padx=8, pady=5)
        controls.columnconfigure(0, weight=1)
        self.input = tk.Entry(controls, font=("sans", 12))
        self.input.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.input.bind("<Return>", lambda event: self.send_text())
        self.send = tk.Button(controls, text="Send", command=self.send_text)
        self.send.grid(row=0, column=1, padx=3)
        self.talk = tk.Button(controls, text="Talk", command=self.toggle_record, bg="#a52d37", fg="white")
        self.talk.grid(row=0, column=2, padx=3)
        options = tk.Frame(controls, bg="#17151c")
        options.grid(row=1, column=0, columnspan=3, sticky="ew")
        for label, variable in (("Voice", self.speak), ("Rituals", self.rituals)):
            tk.Checkbutton(options, text=label, variable=variable, bg="#17151c", fg="#dcc5a1", selectcolor="#24212b").pack(side="left")
        tk.Button(options, text="Bless!", command=self.manual_ritual).pack(side="left", padx=3)
        tk.Button(options, text="Forget", command=self.forget).pack(side="left", padx=3)
        root.after(50, self.tick)

    def show_text(self, text):
        self.reply.configure(state="normal")
        self.reply.delete("1.0", "end")
        self.reply.insert("1.0", text)
        self.reply.configure(state="disabled")

    def set_busy(self, busy, mode="idle"):
        self.busy, self.mode = busy, mode
        self.send.configure(state="disabled" if busy else "normal")
        self.talk.configure(state="normal" if self.recording or not busy else "disabled")

    def worker(self, task, speak: bool):
        try:
            result = task()
            self.events.put(("reply", result))
            if speak and result.get("audio_base64"):
                self.events.put(("speaking", None))
                try:
                    play(base64.b64decode(result["audio_base64"], validate=True), self.set_playback)
                except (OSError, subprocess.SubprocessError, ValueError):
                    self.events.put(("warning", "Text received · speaker playback failed; check audio output"))
        except Exception as error:
            detail = str(error) if isinstance(error, (ClientError, ValueError)) else "Microphone unavailable. Check the device and Pi audio packages."
            self.events.put(("error", detail))
        finally:
            self.events.put(("done", None))

    def start_task(self, task, speak=False):
        self.set_busy(True, "thinking")
        self.status.set("Consulting the tiny cogitator…")
        threading.Thread(target=self.worker, args=(task, speak), daemon=True).start()

    def send_text(self):
        if self.busy:
            return
        text = self.input.get().strip()
        if not text:
            return
        self.input.delete(0, "end")
        self.last_interaction = time.monotonic()
        speak = self.speak.get()
        self.start_task(lambda: self.client.chat(text, speak), speak)

    def toggle_record(self):
        if self.recording:
            self.stop.set()
            self.recording = False
            self.talk.configure(text="Talk", state="disabled")
            self.mode = "thinking"
            self.status.set("Listening finished · consulting the cogitator…")
        elif not self.busy:
            self.last_interaction = time.monotonic()
            self.recording = True
            self.stop = threading.Event()
            self.set_busy(True, "listening")
            self.talk.configure(text="Finish")
            self.status.set("Listening · press Finish when done (15 seconds maximum)")
            speak = self.speak.get()
            def capture():
                recording = record(self.stop)
                self.events.put(("recorded", None))
                if self.closed:
                    raise ValueError("Marsi has closed.")
                return self.client.voice(recording, speak)
            threading.Thread(target=self.worker, args=(capture, speak), daemon=True).start()

    def manual_ritual(self):
        if not self.busy:
            self.last_interaction = time.monotonic()
            speak = self.speak.get()
            self.start_task(lambda: self.client.ritual(speak), speak)

    def forget(self):
        if self.busy:
            return
        from tkinter import messagebox
        if messagebox.askyesno("Forget this session?", "Delete this session's saved conversation and notes on the server?", parent=self.root):
            self.last_interaction = time.monotonic()
            def erase():
                self.client.forget()
                return {"text": "A clean little dataslate. This session's notes and conversation are forgotten.", "animation": "happy"}
            self.start_task(erase)

    def tick(self):
        if self.closed:
            return
        while True:
            try:
                kind, result = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "reply":
                self.animation = result.get("animation", "happy")
                self.animation_until = time.monotonic() + 8
                self.show_text(result["text"])
                self.status.set("Heard: " + result["heard"][:90] if result.get("heard") else "Tiny transmission received")
                if result.get("voice_error"):
                    self.status.set("Text received · " + result["voice_error"])
            elif kind == "speaking":
                self.mode = "speaking"
            elif kind in ("warning", "error"):
                self.status.set(result)
                if kind == "error":
                    self.show_text(result)
            elif kind == "recorded":
                self.recording = False
                self.mode = "thinking"
                self.talk.configure(text="Talk", state="disabled")
                self.status.set("Listening finished · consulting the cogitator…")
            elif kind == "done":
                self.recording = False
                self.talk.configure(text="Talk")
                self.set_busy(False)
                self.last_interaction = time.monotonic()
        now = time.monotonic()
        if self.ambient.due(now, datetime.now().hour, self.last_interaction, self.busy, self.rituals.get()):
            if self.ritual_speech and self.speak.get():
                self.start_task(lambda: self.client.ritual(True), True)
            else:
                result = ritual()
                self.animation = result["animation"]
                self.animation_until = now + 8
                self.show_text(result["text"])
                self.status.set("Marsi performs a tiny ritual")
        self.draw(now)
        self.root.after(100, self.tick)

    def draw(self, now):
        if now > self.animation_until:
            self.animation = "happy"
        c = self.canvas
        c.delete("all")
        w, h = max(1, c.winfo_width()), max(1, c.winfo_height())
        scale = min(w / 480, h / 210)
        x, y = w / 2, h / 2 + math.sin(now * 2) * 2 * scale
        def coords(*values):
            return [x + value * scale if index % 2 == 0 else y + value * scale for index, value in enumerate(values)]
        def oval(*values, **kwargs):
            c.create_oval(*coords(*values), **kwargs)
        def line(*values, **kwargs):
            c.create_line(*coords(*values), **kwargs)
        oval(-82, 78, 82, 93, fill="#100f14", outline="")
        c.create_polygon(*coords(-43, -25, -68, 72, 68, 72, 43, -25), fill="#9a2736", outline="#e85d61", width=2)
        oval(-55, -92, 55, 29, fill="#a62b3b", outline="#e85d61", width=2)
        oval(-40, -67, 40, 9, fill="#262630", outline="#ce9760", width=2)
        blink = int(now * 10) % 57 in (0, 1)
        colour = {"listening": "#74e7a1", "thinking": "#e4b467", "speaking": "#a7daff"}.get(self.mode, "#ffddb0")
        if blink:
            line(-25, -29, -10, -29, fill=colour, width=3)
        else:
            oval(-26, -39, -9, -21, fill=colour, outline="")
        oval(9, -42, 31, -20, fill="#62dcd8", outline="#d6b16f", width=2)
        oval(16, -36, 24, -27, fill="#193b42", outline="")
        line(-10, -8, 0, -3, 10, -8, fill="#ecba92", width=2)
        oval(-33, -12, -21, -7, fill="#bc6170", outline="")
        line(0, 24, 0, 64, fill="#d5a14f", width=3)
        oval(-10, 37, 10, 57, fill="#ddcaa0", outline="")
        oval(-6, 42, -1, 47, fill="#39333a", outline="")
        oval(2, 42, 7, 47, fill="#39333a", outline="")
        arm = -17 + math.sin(now * 5) * 8 if self.animation == "wave" else 15 + math.sin(now * 2) * 5
        line(-45, 28, -73, arm, -82, arm - 15, fill="#bda275", width=5)
        line(45, 28, 73, 17, 85, 30, fill="#bda275", width=5)
        oval(-91, arm - 25, -74, arm - 9, fill="#d0b18b", outline="#39333a")
        line(85, 30, 88, -4, fill="#bda275", width=4)
        oval(78, -20, 98, 0, outline="#cfa763", width=4)
        # A small floating servo-skull and orbiting cog sparks.
        skull_y = -38 + math.sin(now * 2.4) * 5
        oval(86, skull_y - 17, 113, skull_y + 6, fill="#e1cfaa", outline="")
        line(89, skull_y - 10, 110, skull_y - 10, fill="#77624e", width=2)
        for index in range(8):
            angle = now * 0.7 + index * math.pi / 4
            line(140 + math.cos(angle) * 16, 44 + math.sin(angle) * 16,
                 140 + math.cos(angle) * 23, 44 + math.sin(angle) * 23, fill="#916e42", width=2)
        oval(129, 33, 151, 55, outline="#916e42", width=2)
        if self.animation == "blessing":
            for index in range(3):
                sx = -95 + index * 22
                sy = -55 - math.sin(now * 4 + index) * 8
                line(sx - 5, sy, sx + 5, sy, fill="#ffdfa0", width=2)
                line(sx, sy - 5, sx, sy + 5, fill="#ffdfa0", width=2)
        elif self.animation == "doodle":
            c.create_rectangle(*coords(-97, 18, -61, 56), fill="#dbcba2", outline="#95774f", width=2)
            oval(-88, 27, -70, 45, outline="#765331", width=2)
            line(-79, 22, -79, 50, fill="#765331", width=2)
            line(-93, 36, -65, 36, fill="#765331", width=2)
        elif self.animation == "inspect":
            oval(72, skull_y - 26, 125, skull_y + 15, outline="#62dcd8", width=2)

    def close(self):
        self.closed = True
        self.stop.set()
        if self.playback is not None:
            try:
                self.playback.terminate()
            except OSError:
                pass
        self.root.destroy()

    def set_playback(self, process):
        self.playback = process
        if self.closed and process is not None:
            try:
                process.terminate()
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=".env.pi")
    parser.add_argument("--windowed", action="store_true")
    parser.add_argument("--list-audio", action="store_true")
    args = parser.parse_args()
    load_env(args.env)
    if args.list_audio:
        import sounddevice
        print(sounddevice.query_devices())
        return
    import tkinter as tk
    root = tk.Tk()
    try:
        Display(root, Client.from_env(), windowed=args.windowed)
        root.mainloop()
    except (ValueError, tk.TclError) as error:
        root.destroy()
        parser.exit(1, f"Cannot open Marsi: {error}\n")


if __name__ == "__main__":
    main()
