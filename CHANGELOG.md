# Changelog

## Unreleased: Usermac Label Manager

### Added

- **`labels_app.py`**, a second app that only manages usermac labels:
  - a label list with MAC counts, and **Find** by label, MAC or device name;
  - the selected label's MACs, with device names, other labels and the PSKs
    that use the label;
  - **Add MACs...**, **New label...** and **Remove selected**, with an option
    to delete Client List entries left with no labels;
  - the same **Label cleanup...** window as the PSK Manager.
- Launchers `run_labels.bat` (Windows) and `run_labels.command` (macOS / Linux).
- [docs/ADMIN-GUIDE.md](docs/ADMIN-GUIDE.md): setting the apps up for an
  additional network admin, covering tokens, roles, per-user settings and
  who should own Label cleanup.
- `MistClient.untag_usermac`: takes labels off one Client List entry, deleting
  it when none are left if asked. Label cleanup now uses it too.

### Changed

- **`common.py`** (new) holds what both apps share: the base window
  (`MistAppBase`) with the Connection bar, status bar and background calls; the
  MAC/label helpers; and the label cleanup window. `app.py` now builds on it and
  keeps only the PSK-specific code. The PSK Manager behaves the same as before.

## Unreleased: usermac labels and label cleanup

### Added

- **New labels are created as you save.** When a `usermac_labels` PSK names a
  label that no Client List entry carries, a *New usermac labels* dialog asks
  which MACs to tag with it (one per line, optional name after the MAC). MACs
  already in the Client List keep their other labels; unknown MACs get a new
  entry. You can also save without tagging, or cancel. This applies both when
  creating a PSK and when editing a PSK's labels.
- **Existing ▾ menu** next to the labels field. It lists every label in the
  org's Client List with its MAC count; each label opens a submenu with
  *Add 'label'* and the MACs (and names) it currently tags.
- **Edit MACs** for a labels PSK now shows the labels already in the Client List.
- **Label cleanup...** removes MACs from labels after a number of days without
  a connection:
  - default threshold plus per-label overrides (`0` = never prune);
  - optional: only count connections made with a PSK that uses the label;
  - optional: delete Client List entries left with no labels;
  - Preview, then Run now (which re-checks and asks for confirmation);
  - refuses runs above a removal limit (20% by default) and stops on any API
    error before changing anything;
  - logs every removal to `cleanup-log.csv`, and *Undo last run* restores them.
- Local per-org files next to the config: `cleanup-<org_id>.json` (policy) and
  `ledger-<org_id>.json` (last-seen record that extends Mist's ~30-day client
  history).

### API

New `MistClient` methods in `mist_api.py`:

| Method | Endpoint |
| --- | --- |
| `list_usermacs` | `GET /api/v1/orgs/{org_id}/usermacs/search` (follows `next`) |
| `create_usermac` | `POST /api/v1/orgs/{org_id}/usermacs` |
| `update_usermac` | `PUT /api/v1/orgs/{org_id}/usermacs/{usermac_id}` |
| `delete_usermac` | `DELETE /api/v1/orgs/{org_id}/usermacs/{usermac_id}` |
| `tag_usermacs` | adds labels to MACs, creating missing entries |
| `client_sightings` | `GET /api/v1/orgs/{org_id}/clients/search?mac=...&duration=30d` |

### Files

- `cleanup.py` (new): cleanup policy, last-seen ledger, plan, apply, undo. No
  Tk code, so a scheduled command-line mode can reuse it.
- `app.py`: label check before save, `LabelClientsDialog`, Existing menu,
  `CleanupDialog`.
- `mist_api.py`: Client List and client-history calls above.
- `README.md`: new *Usermac labels and label cleanup* section, endpoints,
  verification notes and limitations.

### Not yet done

- Scheduled / command-line cleanup (`--prune`) with Task Scheduler, cron or launchd.
- Clearer wording for the per-label days controls in the cleanup dialog.
