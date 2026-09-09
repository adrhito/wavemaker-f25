"""Feedback: the running log of everything the application has done."""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from app import paths
from Model import Model
from modules.logging.log_utils import log_setup


class Feedback:
    """The Feedback tab."""

    def __init__(self, root: ttk.Notebook, model: Model) -> None:
        self.tab = ttk.Frame(root)
        self.model = model

        self.title_frame = ttk.Frame(self.tab, padding=(25, 20, 25, 0))
        self.content_frame = ttk.Frame(self.tab, padding=(25, 10, 25, 25))
        self.title_frame.grid(row=0, column=0, sticky="w")
        self.content_frame.grid(row=1, column=0, sticky="nsew")

        ttk.Label(self.title_frame, text="Feedback", style="Heading.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            self.title_frame,
            text="Everything the application has done this session. "
            "Messages from INFO upwards are also saved to the logs folder.",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        self.textbox = ScrolledText(self.content_frame, width=150, height=34)
        self.textbox.grid(row=0, column=0, sticky="nsew")
        self.textbox.configure(bg="black", state=tk.DISABLED)

        buttons = ttk.Frame(self.content_frame)
        buttons.grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Button(buttons, text="Open Log Folder", command=self.open_logs).grid(
            row=0, column=0, padx=(0, 10)
        )
        ttk.Button(buttons, text="Copy to Clipboard", command=self.copy).grid(
            row=0, column=1
        )

        log_setup(self.textbox)
        root.add(self.tab, text="   Feedback")

    def onSelect(self) -> None:
        self.textbox.see(tk.END)

    def open_logs(self) -> None:
        paths.ensure_directories()
        try:
            os.startfile(str(paths.LOG_DIR))  # noqa: S606 - the app's own folder
        except OSError as exc:
            self.model.LOGGER.error("Could not open the log folder: %s", exc)

    def copy(self) -> None:
        """Put the whole log on the clipboard, for pasting into an email."""
        self.tab.clipboard_clear()
        self.tab.clipboard_append(self.textbox.get("1.0", tk.END))
        self.model.LOGGER.info("Log copied to the clipboard.")
