"""Pieces shared by the PSK manager (app.py) and the label manager (labels_app.py).

MistAppBase is the window both apps derive from: the Connection bar, the
status bar, and running API calls off the UI thread. The helpers below it
handle MAC addresses and labels; CleanupDialog is the label cleanup window.
"""

from __future__ import annotations

import re
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

import cleanup
import settings
from mist_api import CLOUDS, MistClient, MistError

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
MASK = "•"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

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


SEPARATORS = re.compile(r"[\s:.\-]")
HEX_MAC = re.compile(r"\A[0-9a-f]{12}\Z")
HEX_PATTERN = re.compile(r"\A[0-9a-f]{1,11}\*\Z")
# The PSK form splits its labels field on these, so a label cannot contain them.
LABEL_FORBIDDEN = re.compile(r"[\s,;]")


def normalize_mac(entry):
    """'AA:BB:CC:DD:EE:FF' -> 'aabbccddeeff'. Patterns keep their trailing *."""
    return SEPARATORS.sub("", entry.strip()).lower()


def validate_label(label):
    """Raise ValueError unless `label` can be used as a usermac label."""
    if not label:
        raise ValueError("Enter a label name.")
    if LABEL_FORBIDDEN.search(label):
        raise ValueError("A label cannot contain spaces, commas or semicolons.")
    if len(label) > 64:
        raise ValueError("Keep label names to 64 characters or fewer.")


def parse_label_clients(text):
    """Lines of 'MAC [name]' -> [(mac, name)] for one label. Raises ValueError.

    Client List entries are keyed by one exact MAC, so prefix patterns that a
    'macs' PSK accepts are rejected here.
    """
    clients, seen = [], set()
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        mac = normalize_mac(parts[0])
        if not HEX_MAC.match(mac):
            raise ValueError(
                f"'{parts[0]}' is not a MAC address.\n\n"
                "Use 12 hex digits (aabbccddeeff or aa:bb:cc:dd:ee:ff). "
                "Patterns are not allowed in the Client List."
            )
        if mac not in seen:
            seen.add(mac)
            clients.append((mac, parts[1].strip() if len(parts) > 1 else ""))
    return clients


def format_mac(mac):
    """'aabbccddeeff' -> 'aa:bb:cc:dd:ee:ff'. Anything else is shown as-is."""
    mac = str(mac or "")
    if not HEX_MAC.match(mac):
        return mac
    return ":".join(mac[i:i + 2] for i in range(0, 12, 2))


def group_by_label(usermacs):
    """Client List rows -> {label: [(mac, name)]}, MACs sorted per label."""
    grouped = {}
    for row in usermacs:
        for label in row.get("labels") or []:
            if label:
                grouped.setdefault(label, []).append((row.get("mac") or "", row.get("name") or ""))
    return {label: sorted(clients) for label, clients in grouped.items()}


def tag_summary(tagged):
    created, updated = tagged
    if not (created or updated):
        return ""
    return f" Client List: {created} entr{'y' if created == 1 else 'ies'} added, {updated} updated."


def format_epoch(value):
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return str(value)


def centre_on(window, parent):
    window.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - window.winfo_width()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - window.winfo_height()) // 3
    window.geometry(f"+{max(x, 0)}+{max(y, 0)}")


# --------------------------------------------------------------------------
# base window
# --------------------------------------------------------------------------

class MistAppBase(tk.Tk):
    """Connection bar, status bar and background API calls.

    Subclasses build their own body in row 1 and override the hooks:
    _set_connected (extend it to toggle their buttons), _on_org_changed,
    _on_disconnected and _after_task_error.
    """

    def __init__(self, title, size, minsize):
        super().__init__()
        self.title(title)
        self.geometry(size)
        self.minsize(*minsize)

        self.cfg = settings.load()
        self.client = None
        self.org_id = None
        self.orgs = []          # [(org_id, name)]
        self.pending = 0

        var = tk.StringVar
        self.var_cloud = var(value=self.cfg["cloud_label"])
        self.var_host = var(value=self.cfg["custom_host"])
        self.var_token = var(value=self.cfg["token"])
        self.var_remember = tk.BooleanVar(value=bool(self.cfg["remember_token"]))
        self.var_org = var()
        self.var_status = var(value="Not connected. Paste an API token and press Connect.")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- layout ----------------

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
        self._sync_cloud_state()

    def _build_status_bar(self):
        ttk.Separator(self, orient="horizontal").grid(row=2, column=0, sticky="ew")
        bar = ttk.Frame(self, padding=(12, 6))
        bar.grid(row=3, column=0, sticky="ew")
        ttk.Label(bar, textvariable=self.var_status).pack(side="left")
        # Only shown while a request is in flight; an idle indeterminate bar
        # still paints a block and reads as activity.
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=130)

    def _sync_cloud_state(self):
        custom = self.var_cloud.get() == CUSTOM_CLOUD
        self.ent_host.configure(state="normal" if custom else "disabled")
        self.lbl_host.configure(foreground="" if custom else DISABLED)

    # ---------------- hooks ----------------

    def _set_connected(self, connected):
        self.cmb_org.configure(state="readonly" if connected else "disabled")

    def _on_org_changed(self):
        """The selected org changed; load whatever the window shows."""

    def _on_disconnected(self):
        """Clear whatever the window shows."""

    def _after_task_error(self):
        """Undo UI state that a failed background call may have left behind."""

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
        self._after_task_error()
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
        self.cmb_org.configure(values=[])
        self.var_org.set("")
        self._on_disconnected()
        self._set_connected(False)
        self.var_status.set("Disconnected.")

    def _on_org_selected(self):
        index = self.cmb_org.current()
        if not 0 <= index < len(self.orgs):
            return
        self.org_id = self.orgs[index][0]
        self.cfg["org_id"] = self.org_id
        self._save_config()
        self._on_org_changed()

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


# --------------------------------------------------------------------------
# label cleanup
# --------------------------------------------------------------------------

class CleanupDialog(tk.Toplevel):
    """Prune MACs from usermac labels after N days without a connection.

    The logic lives in cleanup.py; this window edits the per-org policy and
    shows a preview before anything in Mist changes.
    """

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.org_id = app.org_id
        self.policy = cleanup.load_policy(self.org_id)
        self.busy = False

        self.var_default = tk.StringVar(value=str(self.policy["default_days"]))
        self.var_psk_only = tk.BooleanVar(value=bool(self.policy["psk_only"]))
        self.var_delete_empty = tk.BooleanVar(value=bool(self.policy["delete_empty"]))
        self.var_max = tk.StringVar(value=str(self.policy["max_percent"]))
        self.var_label_days = tk.StringVar()
        self.var_summary = tk.StringVar(
            value="Preview shows what would be removed. Nothing changes in Mist until Run now."
        )

        self.title("Label cleanup")
        self.transient(app)
        self.minsize(760, 560)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build()
        self._fill_labels()
        centre_on(self, app)
        self.grab_set()

    # ---------------- layout ----------------

    def _build(self):
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(3, weight=1)

        policy = ttk.LabelFrame(outer, text="Policy", padding=10)
        policy.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        line = ttk.Frame(policy)
        line.pack(anchor="w")
        ttk.Label(line, text="Remove a MAC from a label after").pack(side="left")
        ttk.Entry(line, textvariable=self.var_default, width=5).pack(side="left", padx=4)
        ttk.Label(line, text="days unused").pack(side="left")
        ttk.Label(policy, text="0 = never, unless a label sets its own days.",
                  foreground=MUTED).pack(anchor="w", pady=(0, 6))
        ttk.Checkbutton(
            policy, text="Only count connections made with a PSK that uses the label",
            variable=self.var_psk_only,
        ).pack(anchor="w")
        ttk.Checkbutton(
            policy, text="Delete Client List entries left with no labels",
            variable=self.var_delete_empty,
        ).pack(anchor="w")
        line = ttk.Frame(policy)
        line.pack(anchor="w", pady=(6, 0))
        ttk.Label(line, text="Refuse a run that removes more than").pack(side="left")
        ttk.Entry(line, textvariable=self.var_max, width=4).pack(side="left", padx=4)
        ttk.Label(line, text="% of labelled MACs").pack(side="left")

        labels = ttk.LabelFrame(outer, text="Per-label days", padding=10)
        labels.grid(row=0, column=1, sticky="nsew")
        labels.columnconfigure(0, weight=1)
        self.tree_labels = ttk.Treeview(
            labels, columns=("label", "macs", "days"), show="headings", height=5,
            selectmode="browse",
        )
        for col, text, width in (("label", "Label", 150), ("macs", "MACs", 50),
                                 ("days", "Days", 110)):
            self.tree_labels.heading(col, text=text)
            self.tree_labels.column(col, width=width, anchor="w")
        self.tree_labels.grid(row=0, column=0, sticky="nsew")
        self.tree_labels.bind("<<TreeviewSelect>>", self._on_label_selected)
        line = ttk.Frame(labels)
        line.grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(line, text="Selected:").pack(side="left")
        ttk.Entry(line, textvariable=self.var_label_days, width=5).pack(side="left", padx=4)
        ttk.Button(line, text="Set", command=self.on_set_label).pack(side="left")
        ttk.Button(line, text="Use default", command=self.on_clear_label).pack(
            side="left", padx=(4, 0)
        )

        buttons = ttk.Frame(outer)
        buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 6))
        self.btn_preview = ttk.Button(buttons, text="Preview", command=self.on_preview)
        self.btn_preview.pack(side="left")
        self.btn_run = ttk.Button(buttons, text="Run now", command=self.on_run)
        self.btn_run.pack(side="left", padx=(6, 0))
        self.btn_undo = ttk.Button(buttons, text="Undo last run...", command=self.on_undo)
        self.btn_undo.pack(side="left", padx=(6, 0))
        self.btn_close = ttk.Button(buttons, text="Close", command=self.on_close)
        self.btn_close.pack(side="right")

        ttk.Label(outer, textvariable=self.var_summary, foreground=MUTED,
                  wraplength=720, justify="left").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )

        box = ttk.Frame(outer)
        box.grid(row=3, column=0, columnspan=2, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        columns = ("label", "mac", "name", "seen", "idle", "threshold", "used_by")
        self.tree = ttk.Treeview(box, columns=columns, show="headings", height=10)
        for col, text, width in (
            ("label", "Label", 110), ("mac", "MAC", 125), ("name", "Name", 100),
            ("seen", "Last seen", 120), ("idle", "Idle days", 65),
            ("threshold", "Threshold", 70), ("used_by", "Used by PSKs", 150),
        ):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=yscroll.set)

    # ---------------- per-label days ----------------

    def _fill_labels(self):
        labels = set(self.app.label_clients) | set(self.policy["label_days"])
        self.tree_labels.delete(*self.tree_labels.get_children())
        for label in sorted(labels, key=str.lower):
            count = len(self.app.label_clients.get(label, []))
            if label in self.policy["label_days"]:
                days = int(self.policy["label_days"][label])
                shown = "never" if days == 0 else str(days)
            else:
                shown = f"default ({self.var_default.get().strip() or '?'})"
            self.tree_labels.insert("", "end", iid=label, values=(label, count, shown))

    def _selected_label(self):
        selection = self.tree_labels.selection()
        return selection[0] if selection else None

    def _on_label_selected(self, _event=None):
        label = self._selected_label()
        if label is not None:
            self.var_label_days.set(str(self.policy["label_days"].get(label, "")))

    def on_set_label(self):
        label = self._selected_label()
        if label is None:
            messagebox.showinfo("Per-label days", "Select a label first.", parent=self)
            return
        try:
            days = parse_int(self.var_label_days.get(), "Days", minimum=0)
        except ValueError as exc:
            messagebox.showwarning("Per-label days", str(exc), parent=self)
            return
        if days is None:
            self.on_clear_label()
            return
        self.policy["label_days"][label] = days
        self._fill_labels()
        self.tree_labels.selection_set(label)

    def on_clear_label(self):
        label = self._selected_label()
        if label is None:
            return
        self.policy["label_days"].pop(label, None)
        self.var_label_days.set("")
        self._fill_labels()
        self.tree_labels.selection_set(label)

    # ---------------- policy ----------------

    def _read_policy(self):
        """Validate the form into self.policy and save it. Raises ValueError."""
        default = parse_int(self.var_default.get(), "Days unused", minimum=0)
        cap = parse_int(self.var_max.get(), "Maximum percent", minimum=1)
        if cap is not None and cap > 100:
            raise ValueError("Maximum percent must be between 1 and 100.")
        self.policy.update({
            "default_days": 0 if default is None else default,
            "psk_only": bool(self.var_psk_only.get()),
            "delete_empty": bool(self.var_delete_empty.get()),
            "max_percent": 20 if cap is None else cap,
        })
        cleanup.save_policy(self.org_id, self.policy)
        self._fill_labels()

    # ---------------- background work ----------------

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for widget in (self.btn_preview, self.btn_run, self.btn_undo, self.btn_close):
            widget.configure(state=state)

    def _progress(self, index, total):
        self.app.after(0, lambda: self.app.var_status.set(
            f"Checking client history ... {index}/{total} MACs"
        ))

    def _plan_async(self, then):
        """Save the policy, build a plan off the UI thread, show it, then then(plan)."""
        try:
            self._read_policy()
        except (ValueError, OSError) as exc:
            messagebox.showwarning("Label cleanup", str(exc), parent=self)
            return
        self._set_busy(True)
        client, org_id, policy = self.app.client, self.org_id, dict(self.policy)

        def on_plan(plan):
            self._set_busy(False)
            self._show_plan(plan)
            then(plan)

        self.app._run(
            "Checking client history ...",
            lambda: cleanup.build_plan(client, org_id, policy, progress=self._progress),
            on_plan,
            on_error=lambda: self._set_busy(False),
        )

    def _show_plan(self, plan):
        self.tree.delete(*self.tree.get_children())
        for index, cand in enumerate(plan.candidates):
            seen = format_epoch(cand.last_seen) if cand.last_seen else "not seen"
            self.tree.insert("", "end", iid=str(index), values=(
                cand.label, format_mac(cand.mac), cand.name, seen, cand.idle_days,
                cand.threshold, ", ".join(cand.used_by) or "(none)",
            ))
        count = len(plan.candidates)
        if count:
            text = f"{count} of {plan.pairs} labelled MAC(s) are past their threshold."
        else:
            text = f"Nothing to remove - all {plan.pairs} labelled MAC(s) are within their threshold."
        if plan.over_cap:
            text += (f" That is more than the {plan.max_percent}% limit, so Run now will "
                     "refuse. Check the thresholds, or raise the limit if this is expected.")
        text += (f" 'Not seen' means no connection in Mist's last {cleanup.LOOKBACK_DAYS} "
                 "days of history, nor since this app started tracking.")
        self.var_summary.set(text)
        self.app.var_status.set(f"Label cleanup preview: {count} MAC(s) past threshold.")

    # ---------------- actions ----------------

    def on_preview(self):
        self._plan_async(lambda _plan: None)

    def on_run(self):
        # Always re-plan: a preview from minutes ago may no longer be true.
        self._plan_async(self._confirm_run)

    def _confirm_run(self, plan):
        if not plan.candidates:
            messagebox.showinfo("Label cleanup", "Nothing to remove.", parent=self)
            return
        if plan.over_cap:
            messagebox.showwarning(
                "Label cleanup",
                f"This run would remove {len(plan.candidates)} of {plan.pairs} labelled "
                f"MACs, more than the {plan.max_percent}% limit.\n\nNothing was changed. "
                "Check the thresholds, or raise the limit if this is expected.",
                parent=self,
            )
            return
        labels = sorted({c.label for c in plan.candidates}, key=str.lower)
        lines = "\n".join(
            f"  - {label}: {sum(c.label == label for c in plan.candidates)} MAC(s)"
            for label in labels[:12]
        )
        psks = sorted({name for c in plan.candidates for name in c.used_by if name})
        psk_text = (", ".join(psks[:8]) + (" ..." if len(psks) > 8 else "")) or "(none)"
        if not messagebox.askyesno(
            "Label cleanup",
            f"Remove {len(plan.candidates)} MAC(s) from their labels?\n\n{lines}\n\n"
            f"These devices will stop matching these PSKs: {psk_text}\n\n"
            "Every removal is logged and can be put back with Undo last run.",
            icon="warning", default="no", parent=self,
        ):
            return
        self._set_busy(True)
        client, delete_empty = self.app.client, self.policy["delete_empty"]
        self.app._run(
            f"Removing {len(plan.candidates)} MAC(s) from labels ...",
            lambda: cleanup.apply_plan(client, plan, delete_empty),
            self._after_run,
            on_error=lambda: self._set_busy(False),
        )

    def _after_run(self, result):
        _run_id, count = result
        self._set_busy(False)
        self.tree.delete(*self.tree.get_children())
        self.var_summary.set(
            f"Removed {count} MAC(s). Logged to {cleanup.log_path()} - "
            "Undo last run puts them back."
        )
        self.app.var_status.set(f"Label cleanup removed {count} MAC(s).")
        self.app._load_labels()

    def on_undo(self):
        run_id, rows = cleanup.last_run(self.org_id)
        if not run_id:
            messagebox.showinfo("Undo", "No cleanup run to undo for this org.", parent=self)
            return
        shown = "\n".join(f"  - {row['label']}: {format_mac(row['mac'])} {row['name']}"
                          for row in rows[:12])
        extra = "" if len(rows) <= 12 else f"\n  ... and {len(rows) - 12} more"
        if not messagebox.askyesno(
            "Undo last run",
            f"Put back {len(rows)} label assignment(s) removed on {rows[0]['time']}?"
            f"\n\n{shown}{extra}",
            parent=self,
        ):
            return
        self._set_busy(True)
        client, org_id = self.app.client, self.org_id
        self.app._run(
            "Restoring labels ...",
            lambda: cleanup.undo_run(client, org_id, run_id),
            lambda tagged: self._after_undo(len(rows), tagged),
            on_error=lambda: self._set_busy(False),
        )

    def _after_undo(self, count, tagged):
        self._set_busy(False)
        self.var_summary.set(f"Restored {count} label assignment(s).{tag_summary(tagged)}")
        self.app.var_status.set(f"Label cleanup undone - {count} restored.")
        self.app._load_labels()

    def on_close(self):
        if self.busy:
            return
        try:
            self._read_policy()
        except (ValueError, OSError):
            pass  # keep the last saved policy rather than trap the user here
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
