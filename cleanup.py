"""Remove MACs from usermac labels when they have not connected for N days.

No Tk in here: the GUI's Label cleanup dialog drives it today, and a
scheduled command-line run can reuse it unchanged.

Every decision comes straight from Mist's client history, which reaches back
LOOKBACK_DAYS. Thresholds are therefore capped at MAX_DAYS (= LOOKBACK_DAYS):
past that, Mist cannot tell an idle device from one it has forgotten.

A MAC that Mist has not seen ages from when its Client List entry last
changed (updated_at, never earlier than the label was added), so a newly
tagged device gets the full grace period. Undo also restarts the grace period
for what it puts back.
"""

from __future__ import annotations

import csv
import json
import os
import time
import uuid
from dataclasses import dataclass, field

import settings

DAY = 86400
LOOKBACK_DAYS = 30
LOOKBACK = f"{LOOKBACK_DAYS}d"
MIN_DAYS, MAX_DAYS = 1, LOOKBACK_DAYS   # thresholds; 0 separately means "never"

POLICY_DEFAULTS = {
    "default_days": 30,     # 0 = never prune labels without an override
    "label_days": {},       # label -> days; 0 = never prune this label
    "ssid_only": False,     # only count connections to an SSID whose PSK uses the label
    "delete_empty": False,  # delete a Client List entry once it has no labels left
    "max_percent": 20,      # refuse a run that would remove more than this share
}


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------

def _org_file(prefix, org_id):
    return settings.config_dir() / f"{prefix}-{org_id}.json"


def log_path():
    return settings.config_dir() / "cleanup-log.csv"


def _read_json(path):
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, ValueError):
        pass
    return {}


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def load_policy(org_id):
    policy = dict(POLICY_DEFAULTS)
    stored = _read_json(_org_file("cleanup", org_id))
    if "psk_only" in stored and "ssid_only" not in stored:
        stored["ssid_only"] = stored["psk_only"]   # option renamed
    policy.update({k: v for k, v in stored.items() if k in POLICY_DEFAULTS})
    # Older versions allowed thresholds past what Mist's history can support.
    policy["default_days"] = clamp_days(policy["default_days"])
    policy["label_days"] = {
        label: clamp_days(days) for label, days in dict(policy.get("label_days") or {}).items()
    }
    return policy


def clamp_days(days):
    """Fit a stored threshold into 0 (never) or MIN_DAYS..MAX_DAYS."""
    try:
        days = int(days)
    except (TypeError, ValueError):
        return 0
    return 0 if days <= 0 else min(max(days, MIN_DAYS), MAX_DAYS)


def save_policy(org_id, policy):
    _write_json(_org_file("cleanup", org_id), {k: policy[k] for k in POLICY_DEFAULTS})


def load_restored(org_id):
    """{label|mac: epoch} for pairs Undo put back; their grace period restarts then."""
    return _read_json(_org_file("restored", org_id))


def save_restored(org_id, restored):
    _write_json(_org_file("restored", org_id), restored)


def _key(label, mac):
    return f"{label}|{mac}"


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------

@dataclass
class Candidate:
    label: str
    mac: str
    name: str
    usermac_id: str
    last_seen: float | None     # None: not seen in Mist's last LOOKBACK_DAYS
    idle_days: int
    threshold: int
    used_by: list = field(default_factory=list)  # names of PSKs using the label


@dataclass
class Plan:
    org_id: str
    candidates: list
    pairs: int                  # label/MAC pairs examined
    usermacs: dict              # usermac id -> row, as fetched
    max_percent: int
    psks_by_label: dict

    @property
    def over_cap(self):
        return bool(self.pairs) and len(self.candidates) * 100 > self.max_percent * self.pairs


def threshold_for(policy, label):
    return clamp_days(policy["label_days"].get(label, policy["default_days"]))


def build_plan(client, org_id, policy, now=None, progress=None):
    """Look up every labelled MAC and return what is idle past its threshold.

    Changes nothing in Mist. Any API error
    propagates: a partial view must never turn into "these were unused".
    """
    now = now or time.time()
    usermacs = {row["id"]: row for row in client.list_usermacs(org_id) if row.get("id")}

    psks_by_label = {}
    for psk in client.list_psks(org_id):
        if isinstance(psk, dict) and psk.get("usage") == "usermac_labels":
            for label in psk.get("usermac_labels") or []:
                psks_by_label.setdefault(label, []).append(psk)

    pairs = [
        (label, row)
        for row in usermacs.values()
        for label in row.get("labels") or []
        if label and row.get("mac")
    ]
    # Labels with no threshold are never pruned, so skip their lookups too.
    watched = [(label, row) for label, row in pairs if threshold_for(policy, label)]

    macs = sorted({row["mac"] for _label, row in watched})
    sightings = {}
    for index, mac in enumerate(macs, start=1):
        if progress:
            progress(index, len(macs))
        sightings[mac] = client.client_sightings(org_id, mac, LOOKBACK)

    restored = load_restored(org_id)
    candidates = []
    for label, row in pairs:
        threshold = threshold_for(policy, label)
        if not threshold:
            continue
        mac = row["mac"]
        seen_by_ssid = sightings.get(mac) or {}
        if policy["ssid_only"]:
            ssids = {psk.get("ssid") for psk in psks_by_label.get(label, []) if psk.get("ssid")}
            seen_by_ssid = {ssid: t for ssid, t in seen_by_ssid.items() if ssid in ssids}
        last_seen = max(seen_by_ssid.values(), default=None)

        # Never age a pair from before it could have been tagged or restored.
        tagged = float(row.get("updated_at") or row.get("created_at") or now)
        reference = max(last_seen or 0, tagged, float(restored.get(_key(label, mac), 0)))
        idle = int((now - reference) // DAY)
        if last_seen is None:
            # Beyond the lookback Mist cannot say how long, only "at least this".
            idle = min(idle, LOOKBACK_DAYS)
        if idle >= threshold:
            candidates.append(Candidate(
                label=label, mac=mac, name=row.get("name") or "",
                usermac_id=row["id"], last_seen=last_seen,
                idle_days=idle, threshold=threshold,
                used_by=[psk.get("name") or "" for psk in psks_by_label.get(label, [])],
            ))

    candidates.sort(key=lambda c: (c.label.lower(), -c.idle_days, c.mac))
    return Plan(org_id, candidates, len(pairs), usermacs,
                int(policy["max_percent"]), psks_by_label)


# --------------------------------------------------------------------------
# applying and undoing
# --------------------------------------------------------------------------

LOG_FIELDS = ["run_id", "time", "org_id", "label", "mac", "name",
              "last_seen", "idle_days", "threshold", "action"]


def apply_plan(client, plan, delete_empty):
    """Take each candidate's label off its Client List entry. Returns (run_id, count)."""
    by_entry = {}
    for cand in plan.candidates:
        by_entry.setdefault(cand.usermac_id, []).append(cand)

    run_id = uuid.uuid4().hex[:8]
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    done = []
    try:
        for usermac_id, cands in by_entry.items():
            action = client.untag_usermac(
                plan.org_id, plan.usermacs[usermac_id], [c.label for c in cands], delete_empty
            )
            done.extend((cand, action) for cand in cands)
    finally:
        # Log whatever succeeded, even if a later call failed.
        _append_log(run_id, stamp, plan.org_id, done)
    return run_id, len(done)


def _append_log(run_id, stamp, org_id, done):
    if not done:
        return
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.is_file()
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new:
            writer.writerow(LOG_FIELDS)
        for cand, action in done:
            seen = time.strftime("%Y-%m-%d", time.localtime(cand.last_seen)) if cand.last_seen else ""
            writer.writerow([run_id, stamp, org_id, cand.label, cand.mac, cand.name,
                             seen, cand.idle_days, cand.threshold, action])


def read_log(org_id=None):
    path = log_path()
    if not path.is_file():
        return []
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return [row for row in rows if org_id is None or row.get("org_id") == org_id]


def last_run(org_id):
    """(run_id, [log rows]) for the most recent run against this org, or (None, [])."""
    rows = [row for row in read_log(org_id) if not row.get("action", "").startswith("restored")]
    if not rows:
        return None, []
    run_id = rows[-1]["run_id"]
    return run_id, [row for row in rows if row["run_id"] == run_id]


def undo_run(client, org_id, run_id):
    """Put the labels from one run back. Deleted entries are recreated."""
    rows = [row for row in read_log(org_id) if row["run_id"] == run_id]
    assignments = {}
    for row in rows:
        assignments.setdefault(row["label"], []).append((row["mac"], row.get("name") or ""))
    created, updated = client.tag_usermacs(org_id, assignments)

    # A restored pair starts a fresh grace period instead of being pruned again
    # on the next run. Entries older than the lookback no longer matter.
    now = time.time()
    restored = {
        key: when for key, when in load_restored(org_id).items()
        if now - float(when) < LOOKBACK_DAYS * DAY
    }
    for row in rows:
        restored[_key(row["label"], row["mac"])] = now
    save_restored(org_id, restored)

    _mark_restored(run_id)
    return created, updated


def _mark_restored(run_id):
    path = log_path()
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        if row["run_id"] == run_id and not row["action"].startswith("restored"):
            row["action"] = f"restored ({row['action']})"
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LOG_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)
