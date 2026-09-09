"""Widget styling for the whole application."""

from tkinter import ttk

UNSELECTED_TAB = "#C4C4C4"
SELECTED_TAB = "#777A7A"
NAV_BG = "#444A4A"
BACKGROUND = "black"
FOREGROUND = "white"

THEME_NAME = "Main_Theme"


def style_GUI() -> None:
    """Install the application theme and the named styles built on top of it."""
    main_theme()
    style = ttk.Style()
    style.configure("Heading.TLabel", font=("Helvetica", 20))
    style.configure("Step.TLabel", font=("Helvetica", 11, "bold"), foreground="#9fd4ff")
    style.configure("TCheckbutton", background=BACKGROUND, foreground=FOREGROUND)
    style.map(
        "TCheckbutton",
        background=[("active", BACKGROUND)],
        foreground=[("disabled", UNSELECTED_TAB)],
    )
    style.configure("TProgressbar", background="#7ed957", troughcolor="#2b2b2b")


def main_theme() -> None:
    style = ttk.Style()

    # Creating a theme that already exists raises. The application only builds
    # one window, but the tests and any future second window must not crash on
    # the second call.
    if THEME_NAME not in style.theme_names():
        style.theme_create(
            THEME_NAME,
            parent="alt",
            settings={
                "TNotebook": {
                    "configure": {
                        "tabmargins": [10, 10, 10, 10],
                        "tabposition": "wn",
                        "background": NAV_BG,
                        "borderwidth": "0",
                    }
                },
                "TNotebook.Tab": {
                    "configure": {
                        "padding": [55, 15],
                        "background": UNSELECTED_TAB,
                        "width": "15",
                        "borderwidth": "0",
                        "expand": [1, 1, 1, 1],
                    },
                    "map": {"background": [("selected", SELECTED_TAB)]},
                },
                "TFrame": {
                    "configure": {"background": BACKGROUND, "foreground": FOREGROUND}
                },
                "TLabel": {
                    "configure": {"background": BACKGROUND, "foreground": FOREGROUND}
                },
                "TButton": {
                    "configure": {
                        "foreground": FOREGROUND,
                        "padding": [5, 5],
                        "anchor": "center",
                    },
                    "map": {
                        "background": [
                            ("!active", SELECTED_TAB),
                            ("active", NAV_BG),
                        ],
                        "foreground": [("disabled", "#8a8a8a")],
                    },
                },
                "TEntry": {"configure": {"padding": [1, 3]}},
            },
        )
    style.theme_use(THEME_NAME)
