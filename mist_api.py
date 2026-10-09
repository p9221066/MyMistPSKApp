"""Thin client for the Juniper Mist API, scoped to org-level PSK management."""

from __future__ import annotations

import requests

# Region label -> API host. Verified against Juniper's published cloud list.
CLOUDS = {
    "Global 01 (api.mist.com)": "api.mist.com",
    "Global 02 (api.gc1.mist.com)": "api.gc1.mist.com",
    "Global 03 (api.ac2.mist.com)": "api.ac2.mist.com",
    "Global 04 (api.gc2.mist.com)": "api.gc2.mist.com",
    "Global 05 (api.gc4.mist.com)": "api.gc4.mist.com",
    "EMEA 01 (api.eu.mist.com)": "api.eu.mist.com",
    "EMEA 02 (api.gc3.mist.com)": "api.gc3.mist.com",
    "EMEA 03 (api.ac6.mist.com)": "api.ac6.mist.com",
    "EMEA 04 (api.gc6.mist.com)": "api.gc6.mist.com",
    "APAC 01 (api.ac5.mist.com)": "api.ac5.mist.com",
    "APAC 02 (api.gc5.mist.com)": "api.gc5.mist.com",
    "APAC 03 (api.gc7.mist.com)": "api.gc7.mist.com",
}

USAGE_VALUES = ("multi", "single", "macs", "usermac_labels")


class MistError(Exception):
    """An API call came back with a non-2xx status, or the transport failed."""

    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class MistClient:
    def __init__(self, host, token, timeout=30):
        self.host = host.strip().replace("https://", "").replace("http://", "").strip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Token {token.strip()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    # ---------- plumbing ----------

    def _request(self, method, path, **kwargs):
        url = f"https://{self.host}{path}"
        try:
            resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise MistError(f"Could not reach {self.host}: {exc}") from exc

        if resp.status_code == 401:
            raise MistError("401 Unauthorized - the API token was rejected.", 401)
        if resp.status_code == 403:
            raise MistError(
                "403 Forbidden - the token is valid but lacks rights on this org.", 403
            )

        if not resp.ok:
            raise MistError(_describe_error(resp), resp.status_code, _safe_json(resp))

        if not resp.content:
            return None
        return _safe_json(resp)

    def _paged(self, path, params=None, limit=100):
        """Walk Mist's page/limit pagination and return every row."""
        base = dict(params or {})
        base["limit"] = limit
        page = 1
        rows = []
        while True:
            # A fresh dict per call: the session must not see a later page's number.
            batch = self._request("GET", path, params={**base, "page": page}) or []
            if not isinstance(batch, list):
                return batch
            rows.extend(batch)
            if len(batch) < limit or page >= 100:  # 100-page stop guard
                return rows
            page += 1

    # ---------- identity ----------

    def whoami(self):
        return self._request("GET", "/api/v1/self") or {}

    def orgs(self):
        """[(org_id, name)] for every org this token can see, sorted by name."""
        me = self.whoami()
        found = {}
        for priv in me.get("privileges") or []:
            org_id = priv.get("org_id")
            if not org_id:
                continue
            name = priv.get("name") if priv.get("scope") == "org" else priv.get("org_name")
            found.setdefault(org_id, name or org_id)
        return sorted(found.items(), key=lambda kv: kv[1].lower())

    # ---------- WLANs (to populate the SSID picker) ----------

    def ssids(self, org_id):
        wlans = self._paged(f"/api/v1/orgs/{org_id}/wlans")
        names = {w.get("ssid") for w in wlans if isinstance(w, dict) and w.get("ssid")}
        return sorted(names)

    # ---------- PSKs ----------

    def list_psks(self, org_id, ssid=None, name=None):
        params = {}
        if ssid:
            params["ssid"] = ssid
        if name:
            params["name"] = name
        return self._paged(f"/api/v1/orgs/{org_id}/psks", params)

    def get_psk(self, org_id, psk_id):
        """Fetch one PSK. Used before an edit to work from the freshest copy."""
        return self._request("GET", f"/api/v1/orgs/{org_id}/psks/{psk_id}")

    def create_psk(self, org_id, payload):
        return self._request("POST", f"/api/v1/orgs/{org_id}/psks", json=payload)

    def update_psk(self, org_id, psk_id, payload):
        return self._request("PUT", f"/api/v1/orgs/{org_id}/psks/{psk_id}", json=payload)

    def delete_psks(self, org_id, psk_ids):
        """Bulk delete. Falls back to one-by-one if the bulk route is unavailable."""
        psk_ids = list(psk_ids)
        if not psk_ids:
            return None
        try:
            return self._request(
                "POST", f"/api/v1/orgs/{org_id}/psks/delete", json={"psk_ids": psk_ids}
            )
        except MistError as exc:
            if exc.status not in (404, 405):
                raise
            for psk_id in psk_ids:
                self._request("DELETE", f"/api/v1/orgs/{org_id}/psks/{psk_id}")
            return None

    # ---------- Client List (usermacs) ----------
    #
    # A usermac label is not an object of its own in Mist: it exists only as a
    # tag on a Client List entry, and every entry is keyed by one MAC address.
    # "Creating a label" therefore means tagging MACs with it.

    def list_usermacs(self, org_id, limit=1000):
        """Every Client List entry. The search route pages by a `next` link."""
        path = f"/api/v1/orgs/{org_id}/usermacs/search"
        body = self._request("GET", path, params={"limit": limit}) or {}
        rows = list(body.get("results") or [])
        for _ in range(100):  # stop guard, matching _paged
            next_path = body.get("next")
            if not next_path:
                break
            body = self._request("GET", next_path) or {}
            batch = body.get("results") or []
            if not batch:
                break
            rows.extend(batch)
        return [row for row in rows if isinstance(row, dict)]

    def create_usermac(self, org_id, payload):
        return self._request("POST", f"/api/v1/orgs/{org_id}/usermacs", json=payload)

    def update_usermac(self, org_id, usermac_id, payload):
        return self._request(
            "PUT", f"/api/v1/orgs/{org_id}/usermacs/{usermac_id}", json=payload
        )

    def delete_usermac(self, org_id, usermac_id):
        return self._request("DELETE", f"/api/v1/orgs/{org_id}/usermacs/{usermac_id}")

    def untag_usermac(self, org_id, row, labels, delete_empty=False):
        """Take `labels` off one Client List entry (a row from list_usermacs).

        An entry left with no labels is deleted when `delete_empty`, otherwise
        kept with an empty list. Returns "deleted entry" or "removed label".
        """
        drop = set(labels)
        remaining = [label for label in row.get("labels") or [] if label not in drop]
        if not remaining and delete_empty:
            self.delete_usermac(org_id, row["id"])
            return "deleted entry"
        payload = {
            key: row[key]
            for key in ("mac", "name", "notes", "vlan", "radius_group")
            if row.get(key) not in (None, "")
        }
        payload["labels"] = remaining
        self.update_usermac(org_id, row["id"], payload)
        return "removed label"

    # ---------- client history ----------

    def client_sightings(self, org_id, mac, duration="30d"):
        """(last_seen epoch or None, {psk_id, ...}) for one wireless client MAC.

        Mist keeps only a limited window of client history, so None means "not
        seen within `duration`", not "never connected".
        """
        body = self._request(
            "GET", f"/api/v1/orgs/{org_id}/clients/search",
            params={"mac": mac, "duration": duration, "limit": 10},
        ) or {}
        last_seen, psk_ids = None, set()
        for row in body.get("results") or []:
            if not isinstance(row, dict) or row.get("mac") != mac:
                continue
            stamp = row.get("timestamp")
            if isinstance(stamp, (int, float)) and (last_seen is None or stamp > last_seen):
                last_seen = float(stamp)
            psk_ids.update(pid for pid in row.get("psk_id") or [] if pid)
        return last_seen, psk_ids

    def tag_usermacs(self, org_id, assignments):
        """Add labels to Client List entries, creating entries that are missing.

        `assignments` maps label -> [(mac, name)]. A MAC already in the Client
        List keeps its other labels and gains the new ones; an unknown MAC gets
        a new entry. Returns (created, updated) counts.
        """
        wanted = {}  # mac -> {"labels": [...], "name": str}
        for label, clients in assignments.items():
            for mac, name in clients:
                entry = wanted.setdefault(mac, {"labels": [], "name": ""})
                if label not in entry["labels"]:
                    entry["labels"].append(label)
                entry["name"] = entry["name"] or name
        if not wanted:
            return 0, 0

        existing = {row.get("mac"): row for row in self.list_usermacs(org_id)}
        created = updated = 0
        for mac, entry in wanted.items():
            row = existing.get(mac)
            if row is None:
                payload = {"mac": mac, "labels": entry["labels"]}
                if entry["name"]:
                    payload["name"] = entry["name"]
                self.create_usermac(org_id, payload)
                created += 1
                continue
            labels = list(row.get("labels") or [])
            added = [label for label in entry["labels"] if label not in labels]
            if not added:
                continue
            payload = {
                key: row[key]
                for key in ("mac", "name", "notes", "vlan", "radius_group")
                if row.get(key) not in (None, "")
            }
            payload["labels"] = labels + added
            self.update_usermac(org_id, row["id"], payload)
            updated += 1
        return created, updated


def _safe_json(resp):
    try:
        return resp.json()
    except ValueError:
        return resp.text


def _describe_error(resp):
    """Turn a Mist error response into one readable line."""
    body = _safe_json(resp)
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("message") or body.get("error")
        if not detail and body:
            detail = "; ".join(f"{k}: {v}" for k, v in list(body.items())[:4])
        if detail:
            return f"HTTP {resp.status_code}: {detail}"
    if isinstance(body, str) and body.strip():
        return f"HTTP {resp.status_code}: {body.strip()[:300]}"
    return f"HTTP {resp.status_code} {resp.reason}"
