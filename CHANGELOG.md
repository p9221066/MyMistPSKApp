# Changelog

## Unreleased: dark mode

### Added

- **Theme** selector (Light / Dark / System) in the Connection bar of both
  apps. It applies at once to every open window, including dialogs, lists,
  text boxes and menus, and turns the title bar dark on Windows 10/11. The
  choice is saved and shared by both apps. **System** follows the OS setting
  when the app starts.
- `theme.py` holds the colours and styles. Muted and warning text now use the
  `Muted.TLabel` / `Warn.TLabel` styles, and text boxes and menus are made with
  `theme.text_box()` / `theme.make_menu()`, so they follow the theme.

### Fixed

- The PSK Manager's **Existing** labels button showed two arrows.
- The README's Run table had a duplicate header row.

## Unreleased: label cleanup uses Mist's full 30-day history

### Fixed

- **Label cleanup could remove devices that had connected recently.** Last seen
  came from `clients/search`, which in practice only covers about the last
  week. A device last connected, say, 23 days ago was treated as not seen, so
  it would be removed under a 20 or 25-day threshold. Last seen now combines
  `clients/sessions/search`, which covers 30 days, with `clients/search`, which
  includes devices connected right now.

### Changed

- Thresholds are limited to **1-30 days** (`0` = never), shown next to both
  fields and checked when saved. Settings above 30 are lowered to 30 when
  loaded. The default is now 30 days.
- "Only count connections made with a PSK that uses the label" is now
  "Only count connections to an SSID that has a PSK using the label", because
  the connection history records the SSID but not the PSK. Existing settings
  carry over.
- The local last-seen record (`ledger-<org_id>.json`) is no longer used;
  every decision comes from Mist. Old ledger files can be deleted.
- A device Mist has not seen shows as "not in 30 days" with "30+" idle days.

## Unreleased: Label Manager limited to adding and removing MACs

### Removed

- **New label...** and **Label cleanup...** from the Usermac Label Manager, so
  desktop support staff can only add MACs to and remove MACs from existing
  labels. Both features remain in the PSK Manager. The Add MACs dialog now
  always targets the selected label.

### Added

- **Find MAC** in the Usermac Label Manager: search every label for a full or
  partial MAC, in any format, and see all the labels each match carries.
  Clicking a label opens it with the found MAC selected.
- MACs on more than one label are shown in orange and counted.
- Adding a MAC that already has another label lists those labels and asks first.

### Changed

- The Usermac Label Manager refuses to remove a label's last MAC, since that
  would delete the label and the app can no longer create it again.

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
