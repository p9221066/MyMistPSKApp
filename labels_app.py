"""Mist Usermac Label Manager - add and remove MACs on usermac labels.

A companion to app.py for desktop support staff: pick an existing label,
see the MACs it tags, add MACs to it or take them off. Creating labels and
label cleanup are left to the PSK manager. It shares the API client and
saved login with the PSK manager.

Run with:  python labels_app.py
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from common import (
    MONO_FONT, MistAppBase, centre_on, format_epoch, format_mac, group_by_label,
    normalize_mac, parse_label_clients, tag_summary,
)
import theme
from theme import text_box

# Names in the remove confirmation before "... and N more".
CONFIRM_LIST = 12
HEX_DIGITS = set("0123456789abcdef")


def mac_needle(text):
    """Find MAC text -> lowercase hex digits; "" if blank, None if not part of a MAC.

    Separators are ignored, so aa:bb, aa-bb and aabb all find the same entries.
    """
    compact = normalize_mac(text)
    if not compact:
        return ""
    return compact if set(compact) <= HEX_DIGITS else None


class LabelManagerApp(MistAppBase):
    def __init__(self):
        super().__init__("Mist Usermac Label Manager", "1100x700", (900, 600))
        self.usermacs = []       # Client List rows, as fetched
        self.label_clients = {}  # label -> [(mac, name)]
        self.psks_by_label = {}  # label -> [PSK name]
        self.current = None      # label shown on the right

        self.var_filter = tk.StringVar()
        self.var_filter.trace_add("write", lambda *_: self._fill_labels())
        self.var_used_by = tk.StringVar()
        self.var_macs_title = tk.StringVar(value="MACs")
        self.var_delete_empty = tk.BooleanVar(value=False)
        self.var_mac_find = tk.StringVar()
        self.var_mac_find.trace_add("write", lambda *_: self._show_label())
        self.var_count = tk.StringVar()

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._build_connection_frame()
        self._build_body()
        self._build_status_bar()
        self._set_connected(False)

    # ---------------- layout ----------------

    def _build_body(self):
        body = ttk.Frame(self, padding=(10, 0, 10, 8))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=0, minsize=300)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        # ----- labels -----
        left = ttk.LabelFrame(body, text="Labels", padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        find = ttk.Frame(left)
        find.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        find.columnconfigure(1, weight=1)
        ttk.Label(find, text="Find").grid(row=0, column=0, padx=(0, 6))
        ttk.Entry(find, textvariable=self.var_filter).grid(row=0, column=1, sticky="ew")
        ttk.Label(left, text="Matches a label, MAC or device name.", style="Muted.TLabel").grid(
            row=3, column=0, sticky="w", pady=(4, 0)
        )

        box = ttk.Frame(left)
        box.grid(row=1, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.tree_labels = ttk.Treeview(
            box, columns=("label", "macs"), show="headings", selectmode="browse",
        )
        self.tree_labels.heading("label", text="Label")
        self.tree_labels.heading("macs", text="MACs")
        self.tree_labels.column("label", width=200, anchor="w")
        self.tree_labels.column("macs", width=60, anchor="e")
        self.tree_labels.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(box, orient="vertical", command=self.tree_labels.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.tree_labels.configure(yscrollcommand=yscroll.set)
        self.tree_labels.bind("<<TreeviewSelect>>", self._on_label_selected)
        self.tree_labels.bind("<ButtonRelease-1>", self._on_label_clicked)

        buttons = ttk.Frame(left)
        buttons.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.btn_refresh = ttk.Button(buttons, text="Refresh", command=self.on_refresh)
        self.btn_refresh.pack(side="left")

        # ----- MACs -----
        right = ttk.LabelFrame(body, padding=10)
        right.configure(labelwidget=ttk.Label(right, textvariable=self.var_macs_title))
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)

        find = ttk.Frame(right)
        find.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        find.columnconfigure(1, weight=1)
        ttk.Label(find, text="Find MAC").grid(row=0, column=0, padx=(0, 6))
        self.ent_mac_find = ttk.Entry(find, textvariable=self.var_mac_find, font=MONO_FONT)
        self.ent_mac_find.grid(row=0, column=1, sticky="ew")
        self.ent_mac_find.bind("<Escape>", lambda _e: self.var_mac_find.set(""))
        ttk.Button(find, text="Clear", command=lambda: self.var_mac_find.set("")).grid(
            row=0, column=2, padx=(6, 0)
        )

        ttk.Label(right, textvariable=self.var_used_by, style="Muted.TLabel",
                  wraplength=620, justify="left").grid(row=1, column=0, sticky="w")

        actions = ttk.Frame(right)
        actions.grid(row=2, column=0, sticky="ew", pady=(6, 6))
        self.btn_add = ttk.Button(actions, text="Add MACs...", command=self.on_add_macs)
        self.btn_add.pack(side="left")
        self.btn_remove = ttk.Button(
            actions, text="Remove selected", command=self.on_remove_macs
        )
        self.btn_remove.pack(side="left", padx=(6, 0))
        ttk.Checkbutton(
            actions, text="Delete Client List entries left with no labels",
            variable=self.var_delete_empty,
        ).pack(side="left", padx=(12, 0))

        box = ttk.Frame(right)
        box.grid(row=3, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        columns = ("mac", "name", "other", "updated")
        self.tree_macs = ttk.Treeview(box, columns=columns, show="headings")
        for col, text, width in (
            ("mac", "MAC", 140), ("name", "Name", 150),
            ("other", "Other labels", 180), ("updated", "Updated", 120),
        ):
            self.tree_macs.heading(col, text=text)
            self.tree_macs.column(col, width=width, anchor="w")
        self.tree_macs.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(box, orient="vertical", command=self.tree_macs.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.tree_macs.configure(yscrollcommand=yscroll.set)
        self.tree_macs.bind("<Delete>", lambda _e: self.on_remove_macs())
        # A MAC on more than one label is what Find MAC is meant to catch.
        self.tree_macs.tag_configure("multi", foreground=theme.color("warn"))

        ttk.Label(right, textvariable=self.var_count, style="Muted.TLabel").grid(
            row=4, column=0, sticky="w", pady=(4, 0)
        )

    # ---------------- base-class hooks ----------------

    def _set_connected(self, connected):
        super()._set_connected(connected)
        state = "normal" if connected else "disabled"
        self.btn_refresh.configure(state=state)
        self._sync_mac_buttons()

    def _on_org_changed(self):
        self.current = None
        self.on_refresh()

    def _on_disconnected(self):
        self.usermacs = []
        self.label_clients = {}
        self.psks_by_label = {}
        self.current = None
        self._fill_labels()
        self._show_label()

    def _on_theme_changed(self):
        self.tree_macs.tag_configure("multi", foreground=theme.color("warn"))

    def _sync_mac_buttons(self):
        # Find MAC spans every label, while Add and Remove act on one, so they
        # wait until a label is picked on the left.
        ok = self.client and self.current and not self._mac_lookup_active()
        for widget in (self.btn_add, self.btn_remove):
            widget.configure(state="normal" if ok else "disabled")

    def _mac_lookup_active(self):
        return bool(self.var_mac_find.get().strip())

    # ---------------- loading ----------------

    def on_refresh(self, done_message=None):
        """Reload the Client List; `done_message` replaces the usual status line."""
        if not (self.client and self.org_id):
            return
        org_id = self.org_id

        def work():
            return self.client.list_usermacs(org_id), self.client.list_psks(org_id)

        self._run(
            "Loading Client List ...", work,
            lambda result: self._on_loaded(result, done_message),
        )

    def _on_loaded(self, result, done_message=None):
        usermacs, psks = result
        self.usermacs = usermacs
        self.label_clients = group_by_label(usermacs)
        self.psks_by_label = {}
        for psk in psks:
            if isinstance(psk, dict) and psk.get("usage") == "usermac_labels":
                for label in psk.get("usermac_labels") or []:
                    self.psks_by_label.setdefault(label, []).append(psk.get("name") or "")
        self._fill_labels()
        self._show_label()
        self.var_status.set(done_message or (
            f"Loaded {len(self.label_clients)} label(s) on {len(usermacs)} Client List entr"
            f"{'y' if len(usermacs) == 1 else 'ies'}."
        ))

    # ---------------- labels list ----------------

    def _label_matches(self, label, needle):
        if needle in label.lower():
            return True
        compact = needle.replace(":", "").replace("-", "").replace(".", "")
        return any(
            (compact and compact in mac) or needle in name.lower()
            for mac, name in self.label_clients.get(label, [])
        )

    def _fill_labels(self):
        needle = self.var_filter.get().strip().lower()
        self.tree_labels.delete(*self.tree_labels.get_children())
        for label in sorted(self.label_clients, key=str.lower):
            if needle and not self._label_matches(label, needle):
                continue
            self.tree_labels.insert(
                "", "end", iid=label, values=(label, len(self.label_clients[label]))
            )
        if self.current and self.tree_labels.exists(self.current):
            self.tree_labels.selection_set(self.current)
            self.tree_labels.see(self.current)

    def _on_label_selected(self, _event=None):
        selection = self.tree_labels.selection()
        if selection and selection[0] != self.current:
            self.current = selection[0]
            self._show_label()

    def _on_label_clicked(self, event):
        """Clicking a label during Find MAC opens it with the found MACs selected."""
        label = self.tree_labels.identify_row(event.y)
        if not (label and self._mac_lookup_active()):
            return
        needle = mac_needle(self.var_mac_find.get())
        self.current = label
        self.var_mac_find.set("")          # redraws the label view
        if needle:
            hits = [iid for iid in self.tree_macs.get_children()
                    if needle in normalize_mac(str(self.tree_macs.item(iid)["values"][0]))]
            self.tree_macs.selection_set(hits)
            if hits:
                self.tree_macs.see(hits[0])

    def _show_label(self):
        self.tree_macs.delete(*self.tree_macs.get_children())
        if self._mac_lookup_active():
            self._show_mac_lookup()
            return
        self.tree_macs.heading("other", text="Other labels")
        label = self.current
        if not label:
            self.var_macs_title.set("MACs")
            self.var_used_by.set("Select a label to see the MACs it tags.")
            self.var_count.set("")
            self._sync_mac_buttons()
            return

        self.var_macs_title.set(f"MACs tagged '{label}'")
        psks = self.psks_by_label.get(label, [])
        self.var_used_by.set(
            f"Used by PSK(s): {', '.join(psks)}" if psks else
            "No org PSK uses this label."
        )
        rows = [row for row in self.usermacs if label in (row.get("labels") or [])]
        rows.sort(key=lambda row: row.get("mac") or "")
        for row in rows:
            other = [lab for lab in row.get("labels") or [] if lab != label]
            self.tree_macs.insert("", "end", iid=row["id"], values=(
                format_mac(row.get("mac")), row.get("name") or "",
                ", ".join(other), format_epoch(row.get("updated_at")),
            ), tags=("multi",) if other else ())
        multi = sum(1 for row in rows if len(row.get("labels") or []) > 1)
        self.var_count.set(
            f"{len(rows)} MAC(s)" + (f" - {multi} also on another label" if multi else "")
        )
        self._sync_mac_buttons()

    def _show_mac_lookup(self):
        """Every Client List entry whose MAC contains what was typed, across all labels."""
        text = self.var_mac_find.get().strip()
        needle = mac_needle(text)
        self.tree_macs.heading("other", text="Labels")
        self.var_macs_title.set(f"MACs matching '{text}'")
        self._sync_mac_buttons()
        if needle is None:
            self.var_used_by.set(
                "Not a MAC address. Type hex digits in any format: aabbcc, aa:bb:cc, aa-bb-cc."
            )
            self.var_count.set("")
            return
        self.var_used_by.set(
            "Searching every label. Click a label on the left to add or remove MACs there."
        )
        rows = [row for row in self.usermacs if needle in (row.get("mac") or "")]
        rows.sort(key=lambda row: row.get("mac") or "")
        for row in rows:
            labels = row.get("labels") or []
            self.tree_macs.insert("", "end", iid=row["id"], values=(
                format_mac(row.get("mac")), row.get("name") or "",
                ", ".join(labels) or "(none)", format_epoch(row.get("updated_at")),
            ), tags=("multi",) if len(labels) > 1 else ())
        multi = sum(1 for row in rows if len(row.get("labels") or []) > 1)
        if not rows:
            self.var_count.set("No MAC in the Client List matches.")
        else:
            self.var_count.set(
                f"{len(rows)} MAC(s) match"
                + (f" - {multi} on more than one label" if multi else "")
            )

    # ---------------- actions ----------------

    def on_add_macs(self):
        if self.current:
            AddMacsDialog(self, self.current, self._add)

    def _add(self, label, clients):
        by_mac = {row.get("mac"): row for row in self.usermacs}
        on_other = []
        for mac, _name in clients:
            others = [lab for lab in (by_mac.get(mac) or {}).get("labels") or [] if lab != label]
            if others:
                on_other.append(f"  - {format_mac(mac)}: {', '.join(others)}")
        if on_other:
            extra = len(on_other) - CONFIRM_LIST
            if not messagebox.askyesno(
                "Already on another label",
                f"{len(on_other)} of these MAC(s) already have another label:\n\n"
                + "\n".join(on_other[:CONFIRM_LIST])
                + (f"\n  ... and {extra} more" if extra > 0 else "")
                + f"\n\nAdd them to '{label}' as well? They keep their other labels.",
                icon="warning", default="no", parent=self,
            ):
                return
        org_id = self.org_id
        self.current = label
        self._run(
            f"Tagging {len(clients)} MAC(s) with '{label}' ...",
            lambda: self.client.tag_usermacs(org_id, {label: clients}),
            lambda tagged: self._after_change(f"'{label}' updated.{tag_summary(tagged)}"),
        )

    def on_remove_macs(self):
        label = self.current
        rows_by_id = {row["id"]: row for row in self.usermacs if row.get("id")}
        rows = [rows_by_id[iid] for iid in self.tree_macs.selection() if iid in rows_by_id]
        if not (label and rows):
            messagebox.showinfo("Remove MACs", "Select one or more MACs first.", parent=self)
            return
        # Mist keeps a label only while it tags a MAC, and this app cannot
        # create labels, so emptying one would delete it for good from here.
        tagged = sum(1 for row in self.usermacs if label in (row.get("labels") or []))
        if len(rows) >= tagged:
            messagebox.showwarning(
                "Remove MACs",
                f"'{label}' must keep at least one MAC. Removing "
                f"{'it' if tagged == 1 else 'all of them'} would delete the label.\n\n"
                "Leave one MAC selected out, or ask a network admin to remove the label.",
                parent=self,
            )
            return

        delete_empty = bool(self.var_delete_empty.get())
        shown = "\n".join(
            f"  - {format_mac(row.get('mac'))}  {row.get('name') or ''}".rstrip()
            for row in rows[:CONFIRM_LIST]
        )
        extra = "" if len(rows) <= CONFIRM_LIST else f"\n  ... and {len(rows) - CONFIRM_LIST} more"
        last = sum(1 for row in rows if (row.get("labels") or []) == [label])
        psks = self.psks_by_label.get(label, [])
        notes = []
        if psks:
            notes.append(f"These devices will stop matching PSK(s): {', '.join(psks)}.")
        if last:
            one = last == 1
            fate = "deleted" if delete_empty else "kept with no labels"
            notes.append(
                f"{last} {'has' if one else 'have'} no other label; "
                f"{'its' if one else 'their'} Client List "
                f"{'entry' if one else 'entries'} will be {fate}."
            )
        if not messagebox.askyesno(
            "Remove MACs",
            f"Remove {len(rows)} MAC(s) from '{label}'?\n\n{shown}{extra}\n\n" + "\n".join(notes),
            icon="warning", default="no", parent=self,
        ):
            return

        org_id = self.org_id

        def work():
            return [self.client.untag_usermac(org_id, row, [label], delete_empty) for row in rows]

        self._run(
            f"Removing {len(rows)} MAC(s) from '{label}' ...",
            work,
            lambda actions: self._after_change(
                f"Removed {len(actions)} MAC(s) from '{label}'"
                + (f"; {actions.count('deleted entry')} entr"
                   f"{'y' if actions.count('deleted entry') == 1 else 'ies'} deleted."
                   if "deleted entry" in actions else ".")
            ),
            # A partial failure leaves some MACs changed; reload to show the truth.
            on_error=lambda: self.on_refresh(),
        )

    def _after_change(self, message):
        self.on_refresh(done_message=message)


class AddMacsDialog(tk.Toplevel):
    """Tag MACs with an existing label."""

    def __init__(self, app, label, on_ok):
        super().__init__(app)
        self.app = app
        self.label = label
        self.on_ok = on_ok

        self.title(f"Add MACs to '{label}'")
        self.transient(app)
        self.minsize(420, 340)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        centre_on(self, app)
        self.grab_set()
        self.txt.focus_set()

    def _build(self):
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(2, weight=1)

        ttk.Label(outer, text="Label").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Label(outer, text=self.label, font=("TkDefaultFont", 10, "bold")).grid(
            row=0, column=1, sticky="w"
        )

        ttk.Label(
            outer,
            text="One MAC per line, optionally followed by a device name, e.g. "
                 "'aa:bb:cc:dd:ee:ff printer5'. MACs already in the Client List "
                 "keep their other labels.",
            style="Muted.TLabel", wraplength=400, justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 4))

        self.txt = text_box(
            outer, width=44, height=12, wrap="none", undo=True,
            font=MONO_FONT,
        )
        self.txt.grid(row=2, column=0, columnspan=2, sticky="nsew")

        buttons = ttk.Frame(outer)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Add", command=self.on_add).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=self.close).pack(side="right", padx=(0, 6))

    def on_add(self):
        try:
            clients = parse_label_clients(self.txt.get("1.0", "end"))
        except ValueError as exc:
            messagebox.showwarning("Check the form", str(exc), parent=self)
            return
        if not clients:
            messagebox.showwarning(
                "Check the form",
                "Enter at least one MAC address.",
                parent=self,
            )
            return
        self.close()
        self.on_ok(self.label, clients)

    def close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


def main():
    LabelManagerApp().mainloop()


if __name__ == "__main__":
    main()
