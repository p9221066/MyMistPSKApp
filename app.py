"""MyMistPSKApp - a small desktop GUI for creating org-level PSKs in Juniper Mist.

Run with:  python app.py
"""

from __future__ import annotations

import csv
import re
import secrets
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import settings
from mist_api import CLOUDS, USAGE_VALUES, MistClient, MistError

IS_MAC = sys.platform == "darwin"

# Passphrases and MAC lists are read character by character, so they get a
# monospace face. Each platform ships a different one.
if IS_MAC:
    MONO_FONT = ("Menlo", 12)
elif sys.platform.startswith("win"):
    MONO_FONT = ("Consolas", 10)
else:
    MONO_FONT = ("DejaVu Sans Mono", 10)

# The boxed areas set both colours. Setting only a background leaves the
# foreground to the system, which turns white text onto a light panel under
# macOS dark mode.
BOX_BG = "#f3f3f3"
BOX_FG = "#1a1a1a"
MUTED = "#666666"
WARN = "#a05000"
DISABLED = "#999999"

CUSTOM_CLOUD = "Custom host..."
# Ambiguous glyphs (0/O, 1/l/I) left out so a passphrase can be read aloud.
PASSPHRASE_ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
HEX64 = re.compile(r"\A[0-9a-fA-F]{64}\Z")
MASK = "•"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def generate_passphrase(length=16):
    return "".join(secrets.choice(PASSPHRASE_ALPHABET) for _ in range(length))


def validate_passphrase(value):
    if HEX64.match(value):
        return
    if not 8 <= len(value) <= 63:
        raise ValueError(
            "Passphrase must be 8-63 characters, or exactly 64 hexadecimal characters."
        )


def parse_expiry(text):
    """'YYYY-MM-DD' -> epoch seconds at 23:59:59 local time. Blank -> None."""
    text = text.strip()
    if not text:
        return None
    try:
        day = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise ValueError("Expiry date must be in YYYY-MM-DD format, or left blank.")
    return int(day.replace(hour=23, minute=59, second=59).timestamp())


def parse_int(text, field, minimum=0):
    text = text.strip()
    if not text:
        return None
    if not text.isdigit():
        raise ValueError(f"{field} must be a whole number.")
    value = int(text)
    if value < minimum:
        raise ValueError(f"{field} must be {minimum} or greater.")
    return value


def split_entries(text):
    return [part for part in re.split(r"[\s,;]+", text.strip()) if part]


# Which field holds the bound clients, per usage mode. 'multi' binds nothing.
MAC_FIELDS = {"single": "mac", "macs": "macs", "usermac_labels": "usermac_labels"}
MAC_LIMITS = {"mac": 1, "macs": 5000, "usermac_labels": 100}
# Fields Mist owns; echoing them back on a PUT is pointless or rejected.
SERVER_FIELDS = {
    "id", "org_id", "site_id", "created_time", "modified_time",
    "admin_sso_id", "old_passphrase",
}
SEPARATORS = re.compile(r"[\s:.\-]")
HEX_MAC = re.compile(r"\A[0-9a-f]{12}\Z")
HEX_PATTERN = re.compile(r"\A[0-9a-f]{1,11}\*\Z")


def mac_entries(psk):
    """The MAC addresses / labels bound to a PSK, as a list."""
    field = MAC_FIELDS.get(psk.get("usage"))
    if not field:
        return []
    value = psk.get(field)
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item).strip() for item in value if str(item).strip()]


def mac_summary(psk):
    """One-cell rendering of the bound clients for the list column."""
    usage = psk.get("usage")
    if usage not in MAC_FIELDS:
        return ""
    entries = mac_entries(psk)
    if not entries:
        return "(auto-bind)" if usage == "single" else "(none)"
    if len(entries) == 1:
        return entries[0]
    return f"{entries[0]} +{len(entries) - 1}"


def normalize_mac(entry):
    """'AA:BB:CC:DD:EE:FF' -> 'aabbccddeeff'. Patterns keep their trailing *."""
    return SEPARATORS.sub("", entry.strip()).lower()


def parse_mac_list(text, usage):
    """Lines of text -> a validated list for `usage`. Raises ValueError."""
    field = MAC_FIELDS.get(usage)
    if not field:
        raise ValueError(f"Usage '{usage}' is not bound to MAC addresses.")
    raw = [line.strip() for line in text.splitlines()]
    raw = [line for line in raw if line]

    if usage == "usermac_labels":
        entries = raw                       # labels are free text, not MACs
    else:
        entries = []
        for line in raw:
            cleaned = normalize_mac(line)
            if not (HEX_MAC.match(cleaned) or HEX_PATTERN.match(cleaned)):
                raise ValueError(
                    f"'{line}' is not a MAC address or pattern.\n\n"
                    "Use 12 hex digits (aabbccddeeff or aa:bb:cc:dd:ee:ff), "
                    "or a prefix pattern such as 1122*."
                )
            entries.append(cleaned)

    seen, unique = set(), []
    for entry in entries:
        if entry not in seen:
            seen.add(entry)
            unique.append(entry)

    limit = MAC_LIMITS[field]
    if usage == "single" and len(unique) > 1:
        raise ValueError(
            "Usage 'single' binds one MAC address. Remove the extra lines, or "
            "change the PSK to usage 'macs' in Mist to hold a list."
        )
    if len(unique) > limit:
        raise ValueError(f"Mist allows at most {limit} entries; this list has {len(unique)}.")
    return unique


def format_epoch(value):
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return str(value)


# --------------------------------------------------------------------------
# application
# --------------------------------------------------------------------------

class PSKApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Mist Org PSK Manager")
        self.geometry("1200x760")
        self.minsize(1020, 680)

        self.cfg = settings.load()
        self.client = None
        self.org_id = None
        self.orgs = []          # [(org_id, name)]
        self.psk_rows = []      # raw dicts from the API, index-aligned with the tree
        self.pending = 0
        self._sort_column = None
        self._sort_reverse = False

        self._build_vars()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- variables ----------------

    def _build_vars(self):
        var = tk.StringVar
        self.var_cloud = var(value=self.cfg["cloud_label"])
        self.var_host = var(value=self.cfg["custom_host"])
        self.var_token = var(value=self.cfg["token"])
        self.var_remember = tk.BooleanVar(value=bool(self.cfg["remember_token"]))
        self.var_org = var()

        self.var_name = var()
        self.var_pass = var()
        self.var_show_pass = tk.BooleanVar(value=False)
        self.var_ssid = var()
        self.var_usage = var(value="multi")
        self.var_macs = var()
        self.var_vlan = var()
        self.var_role = var()
        self.var_max_usage = var()
        self.var_expiry = var()
        self.var_notify_expiry = tk.BooleanVar(value=False)
        self.var_notify_days = var(value="7")
        self.var_notify_create = tk.BooleanVar(value=False)
        self.var_email = var()
        self.var_email.trace_add("write", lambda *_: self._sync_email_warning())
        self.var_note = var()

        self.var_filter_ssid = var()
        self.var_filter_name = var()
        self.var_status = var(value="Not connected. Paste an API token and press Connect.")
        self.var_count = var()

    # ---------------- layout ----------------

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._build_connection_frame()

        body = ttk.Frame(self, padding=(10, 0, 10, 0))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=0, minsize=400)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self._build_create_frame(body)
        self._build_list_frame(body)
        self._build_status_bar()

        self._sync_cloud_state()
        self._sync_usage_state()
        self._sync_pass_mask()
        self._sync_notify_state()
        self._set_connected(False)

    def _build_connection_frame(self):
        frame = ttk.LabelFrame(self, text="Connection", padding=10)
        frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 8))
        frame.columnconfigure(3, weight=1)

        ttk.Label(frame, text="Cloud").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.cmb_cloud = ttk.Combobox(
            frame, textvariable=self.var_cloud, state="readonly", width=28,
            values=list(CLOUDS) + [CUSTOM_CLOUD],
        )
        self.cmb_cloud.grid(row=0, column=1, sticky="w")
        self.cmb_cloud.bind("<<ComboboxSelected>>", lambda _e: self._sync_cloud_state())

        self.lbl_host = ttk.Label(frame, text="Host")
        self.lbl_host.grid(row=0, column=2, sticky="w", padx=(12, 6))
        self.ent_host = ttk.Entry(frame, textvariable=self.var_host, width=26)
        self.ent_host.grid(row=0, column=3, sticky="w")

        ttk.Label(frame, text="API token").grid(
            row=1, column=0, sticky="w", padx=(0, 6), pady=(8, 0)
        )
        self.ent_token = ttk.Entry(frame, textvariable=self.var_token, show=MASK, width=48)
        self.ent_token.grid(row=1, column=1, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            frame, text="Remember token on this PC", variable=self.var_remember,
        ).grid(row=1, column=3, sticky="w", padx=(12, 0), pady=(8, 0))

        ttk.Label(frame, text="Organization").grid(
            row=2, column=0, sticky="w", padx=(0, 6), pady=(8, 0)
        )
        self.cmb_org = ttk.Combobox(frame, textvariable=self.var_org, state="disabled", width=46)
        self.cmb_org.grid(row=2, column=1, columnspan=2, sticky="w", pady=(8, 0))
        self.cmb_org.bind("<<ComboboxSelected>>", lambda _e: self._on_org_selected())

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=3, sticky="w", padx=(12, 0), pady=(8, 0))
        self.btn_connect = ttk.Button(buttons, text="Connect", command=self.on_connect)
        self.btn_connect.pack(side="left")
        ttk.Button(buttons, text="Disconnect", command=self.on_disconnect).pack(
            side="left", padx=(6, 0)
        )

    def _build_create_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Create PSK", padding=10)
        frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        frame.columnconfigure(1, weight=1)

        def label(text, row):
            ttk.Label(frame, text=text).grid(row=row, column=0, sticky="w", padx=(0, 6), pady=3)

        def hint(text, row, column=2):
            ttk.Label(frame, text=text, foreground=MUTED).grid(
                row=row, column=column, sticky="w", padx=(6, 0)
            )

        row = 0
        label("Name *", row)
        self.ent_name = ttk.Entry(frame, textvariable=self.var_name)
        self.ent_name.grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)

        row += 1
        label("Passphrase *", row)
        self.ent_pass = ttk.Entry(frame, textvariable=self.var_pass, show=MASK)
        self.ent_pass.grid(row=row, column=1, sticky="ew", pady=3)
        pass_btns = ttk.Frame(frame)
        pass_btns.grid(row=row, column=2, sticky="w", padx=(6, 0))
        ttk.Button(pass_btns, text="Generate", command=self.on_generate).pack(side="left")
        ttk.Checkbutton(
            pass_btns, text="Show", variable=self.var_show_pass, command=self._sync_pass_mask,
        ).pack(side="left", padx=(4, 0))

        row += 1
        label("SSID *", row)
        self.cmb_ssid = ttk.Combobox(frame, textvariable=self.var_ssid)
        self.cmb_ssid.grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)

        row += 1
        label("Usage", row)
        cmb_usage = ttk.Combobox(
            frame, textvariable=self.var_usage, state="readonly", values=list(USAGE_VALUES)
        )
        cmb_usage.grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)
        cmb_usage.bind("<<ComboboxSelected>>", lambda _e: self._sync_usage_state())

        row += 1
        self.lbl_macs = ttk.Label(frame, text="MAC address")
        self.lbl_macs.grid(row=row, column=0, sticky="w", padx=(0, 6), pady=3)
        self.ent_macs = ttk.Entry(frame, textvariable=self.var_macs)
        self.ent_macs.grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)

        row += 1
        self.lbl_macs_hint = ttk.Label(frame, text="", foreground=MUTED)
        self.lbl_macs_hint.grid(row=row, column=1, columnspan=2, sticky="w")

        row += 1
        label("VLAN ID", row)
        ttk.Entry(frame, textvariable=self.var_vlan).grid(row=row, column=1, sticky="ew", pady=3)
        hint("optional", row)

        row += 1
        label("Role", row)
        ttk.Entry(frame, textvariable=self.var_role).grid(row=row, column=1, sticky="ew", pady=3)
        hint("optional", row)

        row += 1
        label("Max concurrent users", row)
        ttk.Entry(frame, textvariable=self.var_max_usage).grid(
            row=row, column=1, sticky="ew", pady=3
        )
        hint("blank = unlimited", row)

        row += 1
        label("Expiry date", row)
        ttk.Entry(frame, textvariable=self.var_expiry).grid(row=row, column=1, sticky="ew", pady=3)
        hint("YYYY-MM-DD", row)

        row += 1
        ttk.Checkbutton(
            frame, text="Remind before expiry", variable=self.var_notify_expiry,
            command=self._sync_notify_state,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 3))
        notify = ttk.Frame(frame)
        notify.grid(row=row, column=2, sticky="w", padx=(6, 0), pady=(8, 3))
        self.ent_notify_days = ttk.Entry(notify, textvariable=self.var_notify_days, width=5)
        self.ent_notify_days.pack(side="left")
        ttk.Label(notify, text="days before").pack(side="left", padx=(4, 0))

        row += 1
        ttk.Checkbutton(
            frame, text="Notify on create / edit", variable=self.var_notify_create,
            command=self._sync_notify_state,
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=3)

        row += 1
        label("Notify email", row)
        ttk.Entry(frame, textvariable=self.var_email).grid(
            row=row, column=1, columnspan=2, sticky="ew", pady=3
        )

        row += 1
        # Advisory only - a missing recipient never blocks Create PSK.
        self.lbl_email_warn = ttk.Label(frame, text="", foreground=WARN, wraplength=240)
        self.lbl_email_warn.grid(row=row, column=1, columnspan=2, sticky="w")

        row += 1
        label("Note", row)
        ttk.Entry(frame, textvariable=self.var_note).grid(
            row=row, column=1, columnspan=2, sticky="ew", pady=3
        )

        row += 1
        actions = ttk.Frame(frame)
        actions.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(14, 4))
        self.btn_create = ttk.Button(actions, text="Create PSK", command=self.on_create)
        self.btn_create.pack(side="left")
        ttk.Button(actions, text="Clear form", command=self.on_clear).pack(side="left", padx=(6, 0))
        self.btn_copy = ttk.Button(
            actions, text="Copy passphrase", command=self.on_copy_passphrase, state="disabled"
        )
        self.btn_copy.pack(side="left", padx=(6, 0))

        row += 1
        # width is set small on purpose: Text defaults to 80 chars, which would
        # force this whole column wide and squeeze the PSK list beside it.
        self.txt_result = tk.Text(
            frame, height=5, width=34, wrap="word", state="disabled", relief="flat",
            background=BOX_BG, foreground=BOX_FG, font=MONO_FONT, padx=6, pady=4,
        )
        self.txt_result.grid(row=row, column=0, columnspan=3, sticky="nsew", pady=(4, 0))
        frame.rowconfigure(row, weight=1)

    def _build_list_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Existing org PSKs", padding=10)
        frame.grid(row=0, column=1, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)

        # Filters and actions sit on separate rows so neither clips when the
        # window is narrow.
        filters = ttk.Frame(frame)
        filters.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(filters, text="SSID").pack(side="left")
        entry_ssid = ttk.Entry(filters, textvariable=self.var_filter_ssid, width=16)
        entry_ssid.pack(side="left", padx=(4, 10))
        ttk.Label(filters, text="Name").pack(side="left")
        entry_name = ttk.Entry(filters, textvariable=self.var_filter_name, width=16)
        entry_name.pack(side="left", padx=(4, 10))
        for entry in (entry_ssid, entry_name):
            entry.bind("<Return>", lambda _e: self.on_refresh())
        self.btn_refresh = ttk.Button(filters, text="Refresh", command=self.on_refresh)
        self.btn_refresh.pack(side="left")

        actions = ttk.Frame(frame)
        actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 8))
        self.btn_macs = ttk.Button(actions, text="Edit MACs", command=self.on_edit_macs)
        self.btn_macs.pack(side="left")
        ttk.Label(actions, text="(or double-click a row)", foreground=MUTED).pack(
            side="left", padx=(6, 12)
        )
        self.btn_delete = ttk.Button(actions, text="Delete selected", command=self.on_delete)
        self.btn_delete.pack(side="left")
        self.btn_export = ttk.Button(actions, text="Export CSV", command=self.on_export)
        self.btn_export.pack(side="left", padx=(6, 0))

        columns = ("name", "ssid", "vlan", "usage", "macs", "role", "max_usage",
                   "expires", "note")
        headings = {
            "name": ("Name", 115), "ssid": ("SSID", 90), "vlan": ("VLAN", 45),
            "usage": ("Usage", 65), "macs": ("MACs", 115), "role": ("Role", 60),
            "max_usage": ("Max", 40), "expires": ("Expires", 100), "note": ("Note", 85),
        }
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        for column in columns:
            text, width = headings[column]
            self.tree.heading(column, text=text, command=lambda c=column: self._sort_by(c))
            self.tree.column(
                column, width=width, anchor="w", stretch=(column in ("name", "note"))
            )
        self.tree.grid(row=2, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", self.on_edit_macs)

        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        yscroll.grid(row=2, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        xscroll.grid(row=3, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        ttk.Label(frame, textvariable=self.var_count, foreground=MUTED).grid(
            row=4, column=0, sticky="w", pady=(6, 0)
        )

    def _build_status_bar(self):
        ttk.Separator(self, orient="horizontal").grid(row=2, column=0, sticky="ew")
        bar = ttk.Frame(self, padding=(12, 6))
        bar.grid(row=3, column=0, sticky="ew")
        ttk.Label(bar, textvariable=self.var_status).pack(side="left")
        # Only shown while a request is in flight; an idle indeterminate bar
        # still paints a block and reads as activity.
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=130)

    # ---------------- widget state ----------------

    def _sync_cloud_state(self):
        custom = self.var_cloud.get() == CUSTOM_CLOUD
        self.ent_host.configure(state="normal" if custom else "disabled")
        self.lbl_host.configure(foreground="" if custom else "#999999")

    def _sync_pass_mask(self):
        self.ent_pass.configure(show="" if self.var_show_pass.get() else MASK)

    def _sync_notify_state(self):
        self.ent_notify_days.configure(
            state="normal" if self.var_notify_expiry.get() else "disabled"
        )
        self._sync_email_warning()

    def _sync_email_warning(self):
        """Point out a notification with no recipient, without blocking it.

        Mist treats `email` as optional, so this stays advisory: the PSK is
        still created, the notification just has nowhere to go.
        """
        if not hasattr(self, "lbl_email_warn"):
            return  # a var trace can fire before the form is built
        wants_notice = self.var_notify_expiry.get() or self.var_notify_create.get()
        missing = wants_notice and not self.var_email.get().strip()
        self.lbl_email_warn.configure(
            text="No recipient set - Mist may not deliver this notification." if missing else ""
        )

    def _sync_usage_state(self):
        hints = {
            "multi": ("MAC address", "Not used when usage is 'multi'."),
            "single": ("MAC address", "One client MAC; blank auto-binds on first use."),
            "macs": ("MAC addresses", "Comma separated; patterns like 1122* allowed."),
            "usermac_labels": ("Usermac labels", "Comma separated labels, e.g. iot, students."),
        }
        usage = self.var_usage.get()
        text, hint = hints.get(usage, hints["multi"])
        self.lbl_macs.configure(text=text)
        self.lbl_macs_hint.configure(text=hint)
        enabled = usage != "multi"
        self.ent_macs.configure(state="normal" if enabled else "disabled")
        self.lbl_macs.configure(foreground="" if enabled else "#999999")

    def _set_connected(self, connected):
        state = "normal" if connected else "disabled"
        for widget in (self.btn_create, self.btn_refresh, self.btn_delete,
                       self.btn_export, self.btn_macs):
            widget.configure(state=state)
        self.cmb_org.configure(state="readonly" if connected else "disabled")

    # ---------------- background work ----------------

    def _busy(self, message):
        self.pending += 1
        self.var_status.set(message)
        if self.pending == 1:
            self.progress.pack(side="right")
            self.progress.start(12)

    def _idle(self, message=None):
        self.pending = max(0, self.pending - 1)
        if self.pending == 0:
            self.progress.stop()
            self.progress.pack_forget()
        if message:
            self.var_status.set(message)

    def _run(self, busy_message, work, on_success, on_error=None):
        """Run work() off the UI thread, then on_success(result) back on it.

        `on_error` lets a caller undo its own in-flight UI state (a disabled
        button, a modal dialog) before the error is reported.
        """
        self._busy(busy_message)

        def worker():
            try:
                result = work()
            except Exception as exc:  # reported to the user in _on_task_error
                self.after(0, lambda e=exc: self._on_task_error(e, on_error))
            else:
                self.after(0, lambda r=result: self._on_task_ok(on_success, r, on_error))

        threading.Thread(target=worker, daemon=True).start()

    def _on_task_ok(self, on_success, result, on_error=None):
        self._idle()
        try:
            on_success(result)
        except Exception as exc:
            self._on_task_error(exc, on_error)

    def _on_task_error(self, exc, on_error=None):
        self._idle("Last operation failed.")
        if on_error is not None:
            try:
                on_error()
            except Exception:
                pass  # a cleanup failure must not mask the original error
        # on_create disables the button while in flight; never leave it stuck off.
        self.btn_create.configure(state="normal" if self.client else "disabled")
        if isinstance(exc, MistError):
            messagebox.showerror("Mist API error", str(exc), parent=self)
        else:
            messagebox.showerror("Error", f"{type(exc).__name__}: {exc}", parent=self)

    # ---------------- connection ----------------

    def _resolve_host(self):
        label = self.var_cloud.get()
        if label == CUSTOM_CLOUD:
            host = self.var_host.get().strip()
            if not host:
                raise ValueError("Enter the API hostname for your custom cloud.")
            return host
        return CLOUDS[label]

    def on_connect(self):
        try:
            host = self._resolve_host()
        except ValueError as exc:
            messagebox.showwarning("Connection", str(exc), parent=self)
            return
        token = self.var_token.get().strip()
        if not token:
            messagebox.showwarning("Connection", "Paste a Mist API token first.", parent=self)
            return

        client = MistClient(host, token)
        self._run(
            f"Connecting to {host} ...",
            client.orgs,
            lambda orgs: self._on_connected(client, host, orgs),
        )

    def _on_connected(self, client, host, orgs):
        if not orgs:
            self.var_status.set("Connected, but no organizations are visible to this token.")
            messagebox.showwarning(
                "Connection",
                "The token authenticated, but it has no organization privileges.",
                parent=self,
            )
            return
        self.client = client
        self.orgs = orgs
        self.cmb_org.configure(values=[f"{name}  [{oid}]" for oid, name in orgs])
        self._set_connected(True)

        saved = self.cfg.get("org_id")
        index = next((i for i, (oid, _n) in enumerate(orgs) if oid == saved), 0)
        self.cmb_org.current(index)
        self.var_status.set(f"Connected to {host} - {len(orgs)} organization(s).")
        self._save_config()
        self._on_org_selected()

    def on_disconnect(self):
        self.client = None
        self.org_id = None
        self.orgs = []
        self.psk_rows = []
        self.tree.delete(*self.tree.get_children())
        self.cmb_org.configure(values=[])
        self.var_org.set("")
        self.var_count.set("")
        self._set_connected(False)
        self.var_status.set("Disconnected.")

    def _on_org_selected(self):
        index = self.cmb_org.current()
        if not 0 <= index < len(self.orgs):
            return
        self.org_id = self.orgs[index][0]
        self.cfg["org_id"] = self.org_id
        self._save_config()
        self._load_ssids()
        self.on_refresh()

    def _load_ssids(self):
        org_id = self.org_id
        self._run("Loading SSIDs ...", lambda: self.client.ssids(org_id), self._on_ssids)

    def _on_ssids(self, ssids):
        self.cmb_ssid.configure(values=ssids)
        if not ssids:
            self.var_status.set("No org-level WLANs found - type the SSID by hand.")

    # ---------------- PSK list ----------------

    def on_refresh(self):
        if not (self.client and self.org_id):
            return
        org_id = self.org_id
        ssid = self.var_filter_ssid.get().strip() or None
        name = self.var_filter_name.get().strip() or None
        self._run(
            "Loading PSKs ...",
            lambda: self.client.list_psks(org_id, ssid=ssid, name=name),
            self._on_psks,
        )

    def _on_psks(self, rows):
        self.psk_rows = [row for row in rows if isinstance(row, dict)]
        self._fill_tree()
        self.var_status.set(f"Loaded {len(self.psk_rows)} PSK(s).")

    def _fill_tree(self):
        self.tree.delete(*self.tree.get_children())
        for index, psk in enumerate(self.psk_rows):
            self.tree.insert("", "end", iid=str(index), values=(
                psk.get("name", ""),
                psk.get("ssid", ""),
                psk.get("vlan_id", ""),
                psk.get("usage", ""),
                mac_summary(psk),
                psk.get("role", ""),
                psk.get("max_usage", ""),
                format_epoch(psk.get("expire_time")),
                psk.get("note", ""),
            ))
        self.var_count.set(f"{len(self.psk_rows)} PSK(s) shown")

    def _sort_by(self, column):
        keys = {
            "name": "name", "ssid": "ssid", "vlan": "vlan_id", "usage": "usage",
            "macs": "usage", "role": "role", "max_usage": "max_usage",
            "expires": "expire_time",
            "note": "note",
        }
        reverse = self._sort_column == column and not self._sort_reverse
        self._sort_column, self._sort_reverse = column, reverse
        key = keys[column]
        self.psk_rows.sort(key=lambda psk: str(psk.get(key) or "").lower(), reverse=reverse)
        self._fill_tree()

    def _selected_psks(self):
        return [
            self.psk_rows[int(iid)]
            for iid in self.tree.selection()
            if iid.isdigit() and int(iid) < len(self.psk_rows)
        ]

    def on_delete(self):
        selected = self._selected_psks()
        if not selected:
            messagebox.showinfo(
                "Delete PSKs", "Select one or more PSKs in the list first.", parent=self
            )
            return
        shown = selected[:12]
        names = "\n".join(
            f"  - {psk.get('name')}  (SSID {psk.get('ssid')})" for psk in shown
        )
        extra = "" if len(selected) <= 12 else f"\n  ... and {len(selected) - 12} more"
        if not messagebox.askyesno(
            "Delete PSKs",
            f"Permanently delete {len(selected)} PSK(s) from this organization?\n\n"
            f"{names}{extra}\n\n"
            "Clients using these passphrases will stop connecting. This cannot be undone.",
            icon="warning", default="no", parent=self,
        ):
            return
        ids = [psk["id"] for psk in selected if psk.get("id")]
        if not ids:
            messagebox.showerror(
                "Delete PSKs", "The selected rows have no PSK id - refresh and retry.", parent=self
            )
            return
        org_id = self.org_id
        self._run(
            f"Deleting {len(ids)} PSK(s) ...",
            lambda: self.client.delete_psks(org_id, ids),
            lambda _result: self._after_delete(len(ids)),
        )

    def _after_delete(self, count):
        self.var_status.set(f"Deleted {count} PSK(s).")
        self.on_refresh()

    # ---------------- MAC list editor ----------------

    def on_edit_macs(self, event=None):
        if event is not None:
            # Ignore double-clicks on headings and separators; those resize/sort.
            if self.tree.identify_region(event.x, event.y) != "cell":
                return
        if not (self.client and self.org_id):
            return
        selected = self._selected_psks()
        if not selected:
            messagebox.showinfo(
                "MAC addresses", "Select a PSK in the list first.", parent=self
            )
            return
        psk = selected[0]
        usage = psk.get("usage")
        if usage not in MAC_FIELDS:
            messagebox.showinfo(
                "MAC addresses",
                f"'{psk.get('name')}' uses usage '{usage}', so it is not bound to "
                "specific clients.\n\nOnly PSKs with usage 'single', 'macs' or "
                "'usermac_labels' carry a client list.",
                parent=self,
            )
            return
        psk_id = psk.get("id")
        if not psk_id:
            messagebox.showerror(
                "MAC addresses", "That row has no PSK id - refresh and retry.", parent=self
            )
            return

        org_id = self.org_id
        # Re-read the PSK so the editor works from Mist's copy, not a stale row.
        self._run(
            f"Loading '{psk.get('name')}' ...",
            lambda: self.client.get_psk(org_id, psk_id),
            lambda fresh: MacEditor(self, fresh if isinstance(fresh, dict) else psk),
        )

    def save_macs(self, dialog, psk_id, payload, count):
        org_id = self.org_id
        self._run(
            "Saving client list ...",
            lambda: self.client.update_psk(org_id, psk_id, payload),
            lambda _result: self._after_mac_save(dialog, count),
            on_error=dialog.set_busy_off,
        )

    def _after_mac_save(self, dialog, count):
        dialog.close()
        self.var_status.set(f"Client list saved - {count} entr{'y' if count == 1 else 'ies'}.")
        self.on_refresh()

    def on_export(self):
        if not self.psk_rows:
            messagebox.showinfo("Export", "Nothing to export - load some PSKs first.", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Export PSK list", defaultextension=".csv",
            filetypes=[("CSV file", "*.csv")], initialfile="mist-org-psks.csv",
        )
        if not path:
            return
        fields = [
            "name", "ssid", "vlan_id", "usage", "role", "max_usage",
            "expire_time", "note", "id",
        ]
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(fields)
                for psk in self.psk_rows:
                    writer.writerow([psk.get(field, "") for field in fields])
        except OSError as exc:
            messagebox.showerror("Export", f"Could not write the file: {exc}", parent=self)
            return
        self.var_status.set(f"Exported {len(self.psk_rows)} PSK(s) to {path}")

    # ---------------- create ----------------

    def on_generate(self):
        self.var_pass.set(generate_passphrase())
        self.var_show_pass.set(True)
        self._sync_pass_mask()

    def _reset_form(self, keep_result=False):
        """Return every input to its starting value.

        `keep_result` leaves the result box and Copy passphrase button alone,
        which is what a successful create needs: the form empties for the next
        key while the passphrase just issued stays on screen to be copied.
        """
        for variable in (
            self.var_name, self.var_pass, self.var_ssid, self.var_macs, self.var_vlan,
            self.var_role, self.var_max_usage, self.var_expiry, self.var_email,
            self.var_note,
        ):
            variable.set("")
        self.var_usage.set("multi")
        self.var_notify_expiry.set(False)
        self.var_notify_create.set(False)
        self.var_notify_days.set("7")
        self.var_show_pass.set(False)
        self._sync_usage_state()
        self._sync_notify_state()
        self._sync_pass_mask()
        if not keep_result:
            self._last_passphrase = ""
            self.btn_copy.configure(state="disabled")
            self._show_result("")

    def on_clear(self):
        self._reset_form()

    def on_copy_passphrase(self):
        passphrase = getattr(self, "_last_passphrase", "")
        if not passphrase:
            return
        self.clipboard_clear()
        self.clipboard_append(passphrase)
        # macOS and X11 hand the clipboard over lazily; without this the text is
        # lost if the app closes before another program asks for it.
        self.update()
        self.var_status.set("Passphrase copied to the clipboard.")

    def _build_payload(self):
        name = self.var_name.get().strip()
        if not name:
            raise ValueError("Name is required.")
        passphrase = self.var_pass.get()
        validate_passphrase(passphrase)
        ssid = self.var_ssid.get().strip()
        if not ssid:
            raise ValueError("SSID is required.")

        usage = self.var_usage.get()
        payload = {"name": name, "passphrase": passphrase, "ssid": ssid, "usage": usage}

        entries = split_entries(self.var_macs.get())
        if usage == "single" and entries:
            payload["mac"] = entries[0]
        elif usage == "macs":
            if not entries:
                raise ValueError("Usage 'macs' needs at least one MAC address or pattern.")
            payload["macs"] = entries
        elif usage == "usermac_labels":
            if not entries:
                raise ValueError("Usage 'usermac_labels' needs at least one label.")
            payload["usermac_labels"] = entries

        vlan = parse_int(self.var_vlan.get(), "VLAN ID", minimum=1)
        if vlan is not None:
            payload["vlan_id"] = vlan

        role = self.var_role.get().strip()
        if role:
            if len(role) > 32:
                raise ValueError("Role must be 32 characters or fewer.")
            payload["role"] = role

        max_usage = parse_int(self.var_max_usage.get(), "Max concurrent users")
        if max_usage is not None:
            payload["max_usage"] = max_usage

        expire = parse_expiry(self.var_expiry.get())
        if expire is not None:
            if expire <= datetime.now().timestamp():
                raise ValueError("The expiry date is in the past.")
            payload["expire_time"] = expire

        if self.var_notify_expiry.get():
            if expire is None:
                raise ValueError("Set an expiry date before enabling the expiry reminder.")
            days = parse_int(self.var_notify_days.get(), "Reminder days", minimum=1)
            payload["notify_expiry"] = True
            payload["expiry_notification_time"] = 7 if days is None else days

        if self.var_notify_create.get():
            payload["notify_on_create_or_edit"] = True

        # Optional even when a notify box is ticked: Mist accepts the PSK either
        # way, so a missing recipient only earns the inline hint on the form.
        email = self.var_email.get().strip()
        if email:
            payload["email"] = email

        note = self.var_note.get().strip()
        if note:
            payload["note"] = note
        return payload

    def on_create(self):
        if not (self.client and self.org_id):
            messagebox.showwarning(
                "Create PSK", "Connect and pick an organization first.", parent=self
            )
            return
        try:
            payload = self._build_payload()
        except ValueError as exc:
            messagebox.showwarning("Check the form", str(exc), parent=self)
            return

        org_id = self.org_id
        passphrase = payload["passphrase"]
        self.btn_create.configure(state="disabled")
        self._run(
            f"Creating PSK '{payload['name']}' ...",
            lambda: self.client.create_psk(org_id, payload),
            lambda created: self._on_created(created, payload, passphrase),
        )

    def _on_created(self, created, payload, passphrase):
        self.btn_create.configure(state="normal")
        created = created if isinstance(created, dict) else {}
        name = created.get("name") or payload["name"]
        ssid = created.get("ssid") or payload["ssid"]
        self._last_passphrase = passphrase
        self.btn_copy.configure(state="normal")
        self._show_result(
            f"Created PSK '{name}' on SSID {ssid}.\n"
            f"Passphrase: {passphrase}\n\n"
            "Copy it now - the list below does not show passphrases."
        )
        self.var_status.set(f"PSK '{name}' created. Form cleared for the next key.")
        self._reset_form(keep_result=True)
        self.ent_name.focus_set()
        self.on_refresh()

    def _show_result(self, text):
        self.txt_result.configure(state="normal")
        self.txt_result.delete("1.0", "end")
        if text:
            self.txt_result.insert("1.0", text)
        self.txt_result.configure(state="disabled")

    # ---------------- config / shutdown ----------------

    def _save_config(self):
        self.cfg.update({
            "cloud_label": self.var_cloud.get(),
            "custom_host": self.var_host.get().strip(),
            "token": self.var_token.get().strip(),
            "remember_token": bool(self.var_remember.get()),
            "org_id": self.org_id or self.cfg.get("org_id", ""),
        })
        try:
            settings.save(self.cfg)
        except OSError as exc:
            self.var_status.set(f"Could not save settings: {exc}")

    def _on_close(self):
        self._save_config()
        self.destroy()


class MacEditor(tk.Toplevel):
    """Edit the client list bound to one PSK.

    Mist's update endpoint requires name, passphrase and ssid alongside the
    change, so this echoes the whole PSK back with only the client list
    swapped. If the API did not hand back the existing passphrase, saving
    would have to set a new one - see _build_passphrase_row.
    """

    def __init__(self, app, psk):
        super().__init__(app)
        self.app = app
        self.psk = psk
        self.usage = psk.get("usage")
        self.field = MAC_FIELDS[self.usage]
        self.limit = MAC_LIMITS[self.field]
        self.is_labels = self.usage == "usermac_labels"
        self.known_passphrase = str(psk.get("passphrase") or "").strip()
        self.var_pass = tk.StringVar()
        self.var_count = tk.StringVar()
        self.busy = False

        noun = "Labels" if self.is_labels else "MAC addresses"
        self.title(f"{noun} - {psk.get('name', '')}")
        self.transient(app)
        self.resizable(True, True)
        self.minsize(420, 400)
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)

        self._build()
        self._centre_on(app)
        self.grab_set()
        self.txt.focus_set()

    # ---------------- layout ----------------

    def _build(self):
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            header, text=self.psk.get("name", ""), font=("TkDefaultFont", 10, "bold"),
        ).pack(side="left")
        ttk.Label(
            header,
            text=f"   SSID {self.psk.get('ssid', '')}   usage '{self.usage}'",
            foreground=MUTED,
        ).pack(side="left")

        hint = (
            "One label per line."
            if self.is_labels else
            "One entry per line. 'aabbccddeeff' or 'aa:bb:cc:dd:ee:ff', "
            "or a prefix pattern such as '1122*'."
        )
        ttk.Label(outer, text=hint, foreground=MUTED, wraplength=430).grid(
            row=1, column=0, sticky="w", pady=(8, 4)
        )

        box = ttk.Frame(outer)
        box.grid(row=3, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.txt = tk.Text(
            box, width=40, height=14, wrap="none", undo=True,
            background=BOX_BG, foreground=BOX_FG, font=MONO_FONT,
            insertbackground=BOX_FG,
        )
        self.txt.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(box, orient="vertical", command=self.txt.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.txt.configure(yscrollcommand=yscroll.set)
        self.txt.insert("1.0", "\n".join(mac_entries(self.psk)))
        self.txt.bind("<<Modified>>", self._on_modified)

        ttk.Label(outer, textvariable=self.var_count, foreground=MUTED).grid(
            row=4, column=0, sticky="w", pady=(6, 0)
        )
        self._update_count()

        self._build_passphrase_row(outer)

        buttons = ttk.Frame(outer)
        buttons.grid(row=6, column=0, sticky="e", pady=(12, 0))
        self.btn_save = ttk.Button(buttons, text="Save", command=self.on_save)
        self.btn_save.pack(side="right")
        self.btn_cancel = ttk.Button(buttons, text="Cancel", command=self.on_cancel)
        self.btn_cancel.pack(side="right", padx=(0, 6))

    def _build_passphrase_row(self, outer):
        """Only shown when Mist withheld the existing passphrase.

        A PUT without one is rejected, so the only way to save is to set a new
        passphrase - which disconnects every client already using the old one.
        That is a surprise worth spelling out rather than burying.
        """
        if self.known_passphrase:
            return
        warn = ttk.Frame(outer)
        warn.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        warn.columnconfigure(1, weight=1)
        ttk.Label(
            warn,
            text="Mist did not return this PSK's passphrase, and its update API "
                 "requires one. Saving will SET the passphrase below, which "
                 "disconnects clients using the current one.",
            foreground=WARN, wraplength=430, justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Label(warn, text="New passphrase").grid(row=1, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(warn, textvariable=self.var_pass).grid(row=1, column=1, sticky="ew")

    def _centre_on(self, parent):
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    # ---------------- state ----------------

    def _on_modified(self, _event=None):
        self.txt.edit_modified(False)
        self._update_count()

    def _update_count(self):
        lines = [line for line in self.txt.get("1.0", "end").splitlines() if line.strip()]
        singular, plural = ("label", "labels") if self.is_labels else ("entry", "entries")
        noun = singular if len(lines) == 1 else plural
        self.var_count.set(f"{len(lines)} {noun}  (Mist allows up to {self.limit})")

    def set_busy_off(self):
        self.busy = False
        for widget in (self.btn_save, self.btn_cancel):
            widget.configure(state="normal")
        self.txt.configure(state="normal")

    def _set_busy_on(self):
        self.busy = True
        for widget in (self.btn_save, self.btn_cancel):
            widget.configure(state="disabled")
        self.txt.configure(state="disabled")

    def close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    # ---------------- actions ----------------

    def on_cancel(self):
        if not self.busy:
            self.close()

    def build_payload(self):
        """The full PSK echoed back with only the client list replaced."""
        entries = parse_mac_list(self.txt.get("1.0", "end"), self.usage)

        passphrase = self.known_passphrase or self.var_pass.get().strip()
        if not passphrase:
            raise ValueError(
                "Mist requires a passphrase to update a PSK, and it did not "
                "return the existing one. Enter a passphrase to continue."
            )
        validate_passphrase(passphrase)

        payload = {
            key: value for key, value in self.psk.items()
            if key not in SERVER_FIELDS and value is not None
        }
        payload["passphrase"] = passphrase
        # Exactly one of these fields applies; stale siblings confuse the API.
        for stale in MAC_FIELDS.values():
            payload.pop(stale, None)
        if self.field == "mac":
            # An empty string means auto-bind on first use, not "leave unset".
            payload["mac"] = entries[0] if entries else ""
        else:
            payload[self.field] = entries
        for required in ("name", "ssid"):
            if not str(payload.get(required) or "").strip():
                raise ValueError(f"This PSK has no {required}; edit it in the Mist UI instead.")
        return payload, entries

    def on_save(self):
        if self.busy:
            return
        try:
            payload, entries = self.build_payload()
        except ValueError as exc:
            messagebox.showwarning("Check the list", str(exc), parent=self)
            return

        if not self.known_passphrase and not messagebox.askyesno(
            "Set a new passphrase?",
            f"Saving will change the passphrase on '{self.psk.get('name')}'.\n\n"
            "Clients using the current passphrase will be disconnected until "
            "they are given the new one.\n\nContinue?",
            icon="warning", default="no", parent=self,
        ):
            return

        self._set_busy_on()
        self.app.save_macs(self, self.psk.get("id"), payload, len(entries))


def main():
    PSKApp().mainloop()


if __name__ == "__main__":
    main()
