"""The look of the application.

Constraints that shaped it
-------------------------
The lab PC is offline Windows 7. Nothing can be installed, so this is plain
``tkinter``/``ttk`` with no theming library, and the only typeface that can be
relied on is Segoe UI, which Windows 7 ships. There is no SF Pro, no font
smoothing control, no vibrancy, no shadows, and ttk on that platform ignores
``background`` on buttons entirely.

What is achievable is restraint: a small neutral palette, one accent colour,
a strict type scale, generous spacing, and hierarchy from tone and spacing
rather than from lines and boxes. Anything with a corner radius is drawn on a
Canvas -- see ``modules/widgets.py``.
"""

from tkinter import ttk


class theme(object):
    """Palette and metrics, as class attributes so they read as constants."""

    # -- surfaces ------------------------------------------------------------
    BACKGROUND = "#1c1c1e"      # the window
    SURFACE = "#242426"         # a card sitting on the window
    SURFACE_2 = "#2c2c2e"       # a control on a card
    SURFACE_3 = "#3a3a3c"       # that control, hovered
    SURFACE_SELECTED = "#48484a"
    DIVIDER = "#38383a"

    # -- text ----------------------------------------------------------------
    LABEL = "#f5f5f7"
    LABEL_SECONDARY = "#98989e"
    LABEL_TERTIARY = "#6e6e73"
    LABEL_DISABLED = "#5a5a5e"

    # -- accents -------------------------------------------------------------
    ACCENT = "#0a84ff"
    ACCENT_HOVER = "#3d9bff"
    ACCENT_DIM = "#1c3a5c"

    SUCCESS = "#30d158"
    WARNING = "#ff9f0a"
    DANGER = "#ff453a"
    DANGER_HOVER = "#ff6b62"
    DANGER_DIM = "#4a2523"

    # -- type ----------------------------------------------------------------
    # Segoe UI is present on every Windows from Vista onward. The Semibold and
    # Light faces ship with Windows 7 too; Tk falls back to Regular if a face
    # is ever missing, which degrades gracefully.
    FAMILY = "Segoe UI"
    TITLE = (FAMILY, 20)
    HEADLINE = ("Segoe UI Semibold", 12)
    BODY = (FAMILY, 10)
    BODY_STRONG = ("Segoe UI Semibold", 10)
    CAPTION = (FAMILY, 9)
    MONO_NUMBER = ("Consolas", 11)

    # -- spacing -------------------------------------------------------------
    GUTTER = 24
    GAP = 12
    TIGHT = 6


# Backwards-compatible names used by older screens.
BACKGROUND = theme.BACKGROUND
FOREGROUND = theme.LABEL
UNSELECTED_TAB = theme.SURFACE_2
SELECTED_TAB = theme.SURFACE_SELECTED
NAV_BG = theme.BACKGROUND

THEME_NAME = "Wavemaker"


def _state_colours():
    from Model import MachineState

    return {
        MachineState.IDLE: ("SELECT PISTONS", theme.LABEL_TERTIARY),
        MachineState.READY: ("READY", theme.ACCENT),
        MachineState.PREPARING: ("PREPARING", theme.WARNING),
        MachineState.HOMED: ("READY TO RUN", theme.SUCCESS),
        MachineState.RUNNING: ("RUNNING", theme.WARNING),
    }


class _LazyStateColours(dict):
    """Filled on first use, because this module must not import Model at import
    time -- Model reaches style indirectly through the screens."""

    def __missing__(self, key):
        self.update(_state_colours())
        return dict.__getitem__(self, key)


STATE_COLOURS = _LazyStateColours()


def style_GUI() -> None:
    """Install the theme and the named styles built on it."""
    main_theme()
    style = ttk.Style()

    style.configure("TLabel", background=theme.BACKGROUND, foreground=theme.LABEL,
                    font=theme.BODY)
    style.configure("Title.TLabel", font=theme.TITLE, foreground=theme.LABEL)
    style.configure("Heading.TLabel", font=theme.TITLE, foreground=theme.LABEL)
    style.configure("Heading2.TLabel", font=theme.HEADLINE, foreground=theme.LABEL)
    style.configure("Group.TLabel", font=theme.CAPTION,
                    foreground=theme.LABEL_TERTIARY)
    style.configure("Dim.TLabel", font=theme.CAPTION,
                    foreground=theme.LABEL_SECONDARY)
    style.configure("Step.TLabel", font=theme.HEADLINE, foreground=theme.LABEL)
    style.configure("Value.TLabel", font=theme.BODY_STRONG, foreground=theme.LABEL)

    # Cards: a slightly lighter surface, no border.
    style.configure("Card.TFrame", background=theme.SURFACE)
    style.configure("Card.TLabel", background=theme.SURFACE, foreground=theme.LABEL,
                    font=theme.BODY)
    style.configure("CardDim.TLabel", background=theme.SURFACE,
                    foreground=theme.LABEL_SECONDARY, font=theme.CAPTION)
    style.configure("CardHeading.TLabel", background=theme.SURFACE,
                    foreground=theme.LABEL, font=theme.HEADLINE)

    style.configure("TEntry", fieldbackground=theme.SURFACE_2,
                    background=theme.SURFACE_2, foreground=theme.LABEL,
                    bordercolor=theme.DIVIDER, lightcolor=theme.SURFACE_2,
                    darkcolor=theme.SURFACE_2, insertcolor=theme.LABEL,
                    padding=[8, 6])
    style.map("TEntry",
              fieldbackground=[("disabled", theme.SURFACE),
                               ("focus", theme.SURFACE_3)],
              foreground=[("disabled", theme.LABEL_DISABLED)])

    style.configure("TCombobox", fieldbackground=theme.SURFACE_2,
                    background=theme.SURFACE_2, foreground=theme.LABEL,
                    arrowcolor=theme.LABEL_SECONDARY, padding=[8, 5])
    style.map("TCombobox",
              fieldbackground=[("readonly", theme.SURFACE_2)],
              foreground=[("disabled", theme.LABEL_DISABLED)])

    style.configure("TCheckbutton", background=theme.BACKGROUND,
                    foreground=theme.LABEL, font=theme.BODY)
    style.map("TCheckbutton", background=[("active", theme.BACKGROUND)],
              foreground=[("disabled", theme.LABEL_DISABLED)])
    style.configure("Card.TCheckbutton", background=theme.SURFACE,
                    foreground=theme.LABEL, font=theme.BODY)
    style.map("Card.TCheckbutton", background=[("active", theme.SURFACE)],
              foreground=[("disabled", theme.LABEL_DISABLED)])

    style.configure("TRadiobutton", background=theme.BACKGROUND,
                    foreground=theme.LABEL, font=theme.BODY)
    style.map("TRadiobutton", background=[("active", theme.BACKGROUND)])

    style.configure("TSeparator", background=theme.DIVIDER)
    style.configure("TProgressbar", background=theme.ACCENT,
                    troughcolor=theme.SURFACE_2, borderwidth=0,
                    lightcolor=theme.ACCENT, darkcolor=theme.ACCENT)

    style.configure("TButton", font=theme.BODY, foreground=theme.LABEL,
                    padding=[10, 6], borderwidth=0, relief="flat")
    style.map("TButton",
              background=[("!active", theme.SURFACE_2), ("active", theme.SURFACE_3)],
              foreground=[("disabled", theme.LABEL_DISABLED)])


def main_theme() -> None:
    style = ttk.Style()
    if THEME_NAME not in style.theme_names():
        style.theme_create(
            THEME_NAME,
            parent="clam",
            settings={
                "TNotebook": {
                    "configure": {
                        "background": theme.BACKGROUND,
                        "borderwidth": 0,
                        "tabmargins": [16, 10, 16, 0],
                    }
                },
                "TNotebook.Tab": {
                    "configure": {
                        "padding": [20, 9],
                        "background": theme.BACKGROUND,
                        "foreground": theme.LABEL_SECONDARY,
                        "borderwidth": 0,
                        "font": ("Segoe UI", 10),
                    },
                    "map": {
                        "background": [("selected", theme.SURFACE_2)],
                        "foreground": [("selected", theme.LABEL)],
                        "expand": [("selected", [0, 0, 0, 0])],
                    },
                },
                "TFrame": {"configure": {"background": theme.BACKGROUND}},
                "TLabel": {
                    "configure": {
                        "background": theme.BACKGROUND,
                        "foreground": theme.LABEL,
                    }
                },
            },
        )
    style.theme_use(THEME_NAME)
