"""Light and dark appearance for both apps.

ttk widgets follow named styles (Muted.TLabel, Warn.TLabel, ...), so they
restyle themselves when the theme changes. Plain tk widgets (Text, Menu) do
not, so they are made with text_box() / make_menu(), which remember them for
apply(). Native message boxes are drawn by the OS and keep its look.

Light mode keeps each platform's native ttk theme. Dark mode switches to
'clam', the built-in theme that honours custom colours on every platform.
"""

from __future__ import annotations

import sys
import tkinter as tk
import weakref
from tkinter import ttk

CHOICES = ("Light", "Dark", "System")

PALETTES = {
    "light": {
        "box_bg": "#f3f3f3", "box_fg": "#1a1a1a",
        "muted": "#666666", "warn": "#a05000", "disabled": "#999999",
    },
    "dark": {
        "bg": "#202124", "fg": "#e8eaed", "field": "#2b2c30", "border": "#3c3d42",
        "heading": "#2f3034", "hover": "#3a3b40", "select_bg": "#3b5f8a", "select_fg": "#ffffff",
        "box_bg": "#2b2c30", "box_fg": "#e8eaed",
        "muted": "#a0a4ab", "warn": "#f0a040", "disabled": "#6b6e75",
    },
}

_mode = "light"
_native_theme = None
_texts = weakref.WeakSet()
_menus = weakref.WeakSet()


def color(name):
    """A colour from the active palette, e.g. color("warn")."""
    return PALETTES[_mode][name]


def is_dark():
    return _mode == "dark"


def resolve(choice):
    """'Light' / 'Dark' / 'System' -> 'light' or 'dark'."""
    choice = (choice or "Light").lower()
    if choice == "system":
        return "dark" if system_prefers_dark() else "light"
    return "dark" if choice == "dark" else "light"


def system_prefers_dark():
    """Best effort; anything unreadable counts as light."""
    try:
        if sys.platform.startswith("win"):
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            with key:
                return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        if sys.platform == "darwin":
            import subprocess
            out = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True, text=True, timeout=2,
            )
            return "dark" in out.stdout.lower()
        import subprocess
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True, text=True, timeout=2,
        )
        return "dark" in out.stdout.lower()
    except Exception:
        return False


# --------------------------------------------------------------------------
# plain tk widgets
# --------------------------------------------------------------------------

def text_box(parent, **options):
    """A tk.Text in the box colours, kept in step with the theme."""
    widget = tk.Text(parent, **options)
    _paint_text(widget)
    _texts.add(widget)
    return widget


def make_menu(parent, **options):
    """A tk.Menu kept in step with the theme."""
    widget = tk.Menu(parent, **options)
    _paint_menu(widget)
    _menus.add(widget)
    return widget


def _paint_text(widget):
    widget.configure(
        background=color("box_bg"), foreground=color("box_fg"),
        insertbackground=color("box_fg"),
    )
    if is_dark():
        widget.configure(selectbackground=color("select_bg"), selectforeground=color("select_fg"))
    else:
        widget.configure(selectbackground="SystemHighlight" if sys.platform.startswith("win")
                         else "#3875d7", selectforeground="white")


def _paint_menu(widget):
    if is_dark():
        widget.configure(
            background=color("field"), foreground=color("fg"),
            activebackground=color("select_bg"), activeforeground=color("select_fg"),
            disabledforeground=color("disabled"),
        )
    else:
        # Empty strings fall back to the platform's own menu colours.
        defaults = tk.Menu(widget)
        for option in ("background", "foreground", "activebackground",
                       "activeforeground", "disabledforeground"):
            widget.configure(**{option: defaults.cget(option)})
        defaults.destroy()


# --------------------------------------------------------------------------
# applying
# --------------------------------------------------------------------------

def apply(root, choice):
    """Switch every open window of `root` to `choice` ('Light', 'Dark' or 'System')."""
    global _mode, _native_theme
    style = ttk.Style(root)
    if _native_theme is None:
        _native_theme = style.theme_use()
    _mode = resolve(choice)

    if is_dark():
        style.theme_use("clam")
        _style_dark(style)
    else:
        style.theme_use(_native_theme)
    _style_common(style)
    _option_db(root)

    root.configure(background=color("bg") if is_dark() else _default_bg(root))
    for registry, paint in ((_texts, _paint_text), (_menus, _paint_menu)):
        for widget in list(registry):
            try:
                if widget.winfo_exists():
                    paint(widget)
            except tk.TclError:          # its window has been closed
                registry.discard(widget)
    for window in [root, *_toplevels(root)]:
        if isinstance(window, tk.Toplevel):
            window.configure(background=color("bg") if is_dark() else _default_bg(root))
        dark_title_bar(window, redraw=True)


def prepare_window(window):
    """Call for each new Toplevel so its frame and title bar match the theme."""
    if is_dark():
        window.configure(background=color("bg"))
    dark_title_bar(window)


def _style_common(style):
    """Styles both palettes share; colours come from the active one."""
    style.configure("Muted.TLabel", foreground=color("muted"))
    style.configure("Warn.TLabel", foreground=color("warn"))
    for name in ("TLabel", "Muted.TLabel", "Warn.TLabel"):
        style.map(name, foreground=[("disabled", color("disabled"))])


def _style_dark(style):
    bg, fg, field = color("bg"), color("fg"), color("field")
    border, sel_bg, sel_fg = color("border"), color("select_bg"), color("select_fg")
    style.configure(
        ".", background=bg, foreground=fg, fieldbackground=field, bordercolor=border,
        darkcolor=bg, lightcolor=border, troughcolor=field, focuscolor=sel_bg,
        selectbackground=sel_bg, selectforeground=sel_fg, insertcolor=fg,
        arrowcolor=fg,
    )
    style.map(".", foreground=[("disabled", color("disabled"))],
              background=[("disabled", bg)])
    style.configure("TLabelframe", background=bg, bordercolor=border)
    style.configure("TLabelframe.Label", background=bg, foreground=fg)
    style.configure("TButton", background=color("heading"), bordercolor=border)
    style.map("TButton", background=[("disabled", bg), ("pressed", sel_bg),
                                     ("active", color("hover"))])
    style.configure("TMenubutton", background=color("heading"))
    style.map("TMenubutton", background=[("active", color("hover"))])
    style.map("TCheckbutton", background=[("active", bg)],
              indicatorbackground=[("selected", sel_bg), ("!selected", field)])
    style.configure("TCheckbutton", indicatorbackground=field, indicatorforeground=fg)
    style.configure("TEntry", fieldbackground=field, foreground=fg, insertcolor=fg)
    style.map("TEntry", fieldbackground=[("disabled", bg)])
    style.configure("TCombobox", fieldbackground=field, foreground=fg,
                    background=color("heading"), arrowcolor=fg)
    style.map("TCombobox",
              fieldbackground=[("readonly", field), ("disabled", bg)],
              foreground=[("disabled", color("disabled"))],
              selectbackground=[("readonly", field)],
              selectforeground=[("readonly", fg)])
    style.configure("Treeview", background=field, fieldbackground=field, foreground=fg,
                    bordercolor=border)
    style.map("Treeview", background=[("selected", sel_bg)],
              foreground=[("selected", sel_fg)])
    style.configure("Treeview.Heading", background=color("heading"), foreground=fg,
                    bordercolor=border)
    style.map("Treeview.Heading", background=[("active", color("hover"))])
    style.configure("TScrollbar", background=color("heading"), troughcolor=bg,
                    bordercolor=border, arrowcolor=fg)
    style.map("TScrollbar", background=[("active", color("hover"))])
    style.configure("Horizontal.TProgressbar", background=sel_bg, troughcolor=field)
    style.configure("TSeparator", background=border)


def _option_db(root):
    """Colours for tk widgets Tk creates on its own, like a combobox's drop-down list."""
    if is_dark():
        for pattern, value in (
            ("*TCombobox*Listbox.background", color("field")),
            ("*TCombobox*Listbox.foreground", color("fg")),
            ("*TCombobox*Listbox.selectBackground", color("select_bg")),
            ("*TCombobox*Listbox.selectForeground", color("select_fg")),
        ):
            root.option_add(pattern, value)
    else:
        root.option_clear()
    # Drop-downs already built keep their old colours; restyle them too.
    for combo in _descendants(root, ttk.Combobox):
        try:
            listbox = combo.tk.call("ttk::combobox::PopdownWindow", combo) + ".f.l"
            if is_dark():
                combo.tk.call(listbox, "configure", "-background", color("field"),
                              "-foreground", color("fg"),
                              "-selectbackground", color("select_bg"),
                              "-selectforeground", color("select_fg"))
            else:
                combo.tk.call(listbox, "configure", "-background", "white",
                              "-foreground", "black")
        except tk.TclError:
            pass


def _default_bg(root):
    probe = tk.Frame(root)
    value = probe.cget("background")
    probe.destroy()
    return value


def _descendants(widget, kind):
    for child in widget.winfo_children():
        if isinstance(child, kind):
            yield child
        yield from _descendants(child, kind)


def _toplevels(root):
    return [w for w in _descendants(root, tk.Toplevel)]


def dark_title_bar(window, redraw=False):
    """Windows 10/11 only: draw the title bar dark to match. Ignored elsewhere."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if is_dark() else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE, and its pre-20H1 id
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
            ) == 0:
                break
        # An open window repaints its title bar only when redrawn.
        if redraw and window.winfo_viewable():
            window.withdraw()
            window.deiconify()
    except Exception:
        pass
