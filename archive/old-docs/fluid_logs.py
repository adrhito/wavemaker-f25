from io import TextIOWrapper
from logging import LogRecord, addLevelName, Handler, DEBUG, CRITICAL, Formatter, getLogger
import tkinter as tk
from datetime import date
from os import getcwd
from tkinter import NORMAL
from tkinter.scrolledtext import ScrolledText

addLevelName(15, "SUCCESS")
LOGGER_NAME = "fluidloggers"

# custom handlers

class TextboxLogHandler(Handler):
    """Emits all levels of logs to the desired tkinter textbox.

        In the future, this may be modified to work better with a scrollable text box.
    """
    text: ScrolledText

    def __init__(self, text: ScrolledText):
        Handler.__init__(self)
        self.text = text
        self.text.tag_configure("SUCCESS", foreground="lime green")
        self.text.tag_configure("DEBUG", foreground="light blue")
        self.text.tag_configure("INFO", foreground="light blue")
        self.text.tag_configure("WARNING", foreground="orange")
        self.text.tag_configure("ERROR", foreground="red")
        self.text.tag_configure(
            "CRITICAL", foreground="crimson", underline=True)
        self.text.configure(state=tk.DISABLED)

    def emit(self, record: LogRecord):
        msg = self.format(record)
        self.text.configure(state=tk.NORMAL)
        self.text.insert(tk.END, msg + "\n")
        self.__apply_coloring()
        self.text.configure(state=tk.DISABLED)

    def __apply_coloring(self):
        """Applies text coloring to recently added line of log."""
        text_metadata = self.text.dump(
            "end-2c linestart", "end", text=True)[0]  # grabs relevant details from dump
        # gets the line # of text that was just entered
        linestart_idx: str = text_metadata[2].split(".")[0]

        line_str = text_metadata[1]
        label_start = line_str.index("#") + 1
        label_end = line_str.index(" -")

        self.text.tag_add(line_str[label_start:label_end], linestart_idx +
                          "." + "0", linestart_idx + "." + str(label_end))


class FileLogHandler(Handler):
    """Emits critical logs (level >= 50) to a specified file for persistant storage."""

    file: str

    def __init__(self, file: str):
        Handler.__init__(self)
        self.file = file

    def emit(self, record: LogRecord):
        handle: TextIOWrapper
        try:
            handle = open(self.file, 'a+')
        except OSError:
            handle = open(self.file, 'x')

        msg = self.format(record)
        handle.write(msg + "\n")
        handle.close()


def log_setup(t: tk.Text):

    # create logger
    logger = getLogger(LOGGER_NAME)
    logger.setLevel(DEBUG)

    th = TextboxLogHandler(t)
    th.setLevel(DEBUG)

    fh = FileLogHandler(f"{getcwd()}/logs/{str(date.today())}.log")
    fh.setLevel(CRITICAL)

    # create formatter
    formatter = Formatter(
        '%(asctime)s %(name)s#%(levelname)s - %(message)s', datefmt="%m/%d/%Y at %I:%M:%S %p")

    th.setFormatter(formatter)
    fh.setFormatter(formatter)

    logger.addHandler(th)
    logger.addHandler(fh)

    return (th, fh)


def action(msg: str):
    logger = getLogger(LOGGER_NAME)
    logger.log(15, "Started successfully")
    logger.debug(msg)
    logger.info(msg)
    logger.warning(msg)
    logger.error(msg)
    logger.critical(msg)
