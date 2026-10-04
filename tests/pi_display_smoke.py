"""Optional: run under Xvfb on Linux to check real Tk widgets and animation."""
import tkinter as tk
from marsi_local.client import Client
from marsi_local.pi import Display

root = tk.Tk()
display = Display(root, Client("http://127.0.0.1:8765"), windowed=True)
for width, height in ((800, 480), (480, 320)):
    root.geometry(f"{width}x{height}")
    root.update()
    for mode in ("idle", "listening", "thinking", "speaking"):
        display.mode = mode
        display.draw(123.4)
        root.update()
        assert display.canvas.find_all(), "The character should be visible"
        assert display.reply.winfo_height() > 20, "Reply area must fit on screen"
        assert display.talk.winfo_viewable(), "Talk control must be visible"
display.close()
print("Pi display smoke test passed at 800x480 and 480x320.")
