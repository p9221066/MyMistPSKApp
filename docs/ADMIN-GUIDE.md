# Using the apps as an additional network admin

This guide is for a network admin who has been asked to look after usermac
labels (and possibly PSKs) in a Mist org that someone else already manages with
these apps. It covers setup, the API token you need, and how to avoid getting
in each other's way.

## In short

- Run your own copy on your own computer, with **your own** API token.
- The token's role decides what you can change. The app does not limit it.
- Agree on **one person** who runs Label cleanup for the org.

## 1. Install

You need Python 3.9 or newer with Tkinter. See
[Install](../README.md#install) in the README for platform notes.

```
git clone https://github.com/p9221066/MyMistPSKApp.git
cd MyMistPSKApp
pip install -r requirements.txt
```

Then start the app you need:

| App | Windows | macOS / Linux |
| --- | --- | --- |
| Usermac Label Manager | `run_labels.bat` | `./run_labels.command` |
| PSK Manager | `run.bat` | `./run.command` |

## 2. Create your own API token

In the Mist dashboard: account menu (top right) → **My Account** →
**API Tokens** → **Create Token**. Paste it into the app's **API token** field
and press **Connect**.

Use a token created under **your own** account, never a colleague's:

- Mist's audit log records every change against the token's owner, so your
  changes stay traceable to you.
- If you leave or the token leaks, it can be revoked without affecting anyone else.

## 3. What your token allows

A token carries the role of the account that created it. Mist roles apply
to the whole org; they cannot be limited to the Client List.

| Task | Role needed |
| --- | --- |
| Browse labels, MACs and PSKs | Any role on the org, including Observer |
| Add or remove MACs, create labels, run Label cleanup | Write access to the org's Client List. Expected to be **Network Admin** or **Super User**; not yet tested with other roles |

Without the right role, changes fail with **403 Forbidden** and nothing is modified.

**The Usermac Label Manager limits what is on screen, not what the token
can do.** A token that can edit the Client List can usually also create and
delete PSKs, through the PSK Manager or any other tool. If that matters, give
the admin the smallest role that works and review Mist's audit log.

## 4. Where your settings live

Everything is saved in your own user profile, not in the app folder:

| Platform | Folder |
| --- | --- |
| Windows | `%LOCALAPPDATA%\MyMistPSKApp\` |
| macOS | `~/Library/Application Support/MyMistPSKApp/` |
| Linux | `~/.config/MyMistPSKApp/` |

| File | Contents |
| --- | --- |
| `config.json` | Cloud, org and, if **Remember token on this PC** is ticked, your token **in plain text** |
| `cleanup-<org_id>.json` | Your Label cleanup settings for that org |
| `ledger-<org_id>.json` | Your record of when each labelled MAC was last seen |
| `cleanup-log.csv` | Every MAC that your cleanup runs removed, used by **Undo last run** |

So:

- Admins on **different computers, or different accounts on the same computer**,
  are completely separate. This is the recommended setup.
- Admins who **share one computer account** would share the saved token. Avoid
  that, or untick **Remember token on this PC** and paste the token each time.

## 5. Working alongside other admins

**Adding and removing MACs** is safe for several admins at once. Each change
goes straight to Mist. Press **Refresh** to see changes made by others.

**Label cleanup should have one owner per org.** Each admin's copy keeps its
own cleanup settings, last-seen record and undo log. If two admins both run
cleanup on the same org:

- they may use different thresholds without realising it;
- each one's last-seen record covers only the times *they* ran it, so a MAC
  can look idle to one admin while the other saw it recently;
- **Undo last run** only undoes the runs made from that copy. It cannot undo
  a colleague's run.

Agree who runs cleanup. Everyone else can still preview it, since **Preview**
changes nothing in Mist, but should not press **Run now**.

## 6. If something goes wrong

- **A MAC was removed by mistake.** Add it back with **Add MACs...**, or, if it
  was removed by your own cleanup run, use **Undo last run** in Label cleanup.
- **403 Forbidden.** Your token's role cannot make that change. Ask an org
  Super User.
- **401 Unauthorized.** The token is wrong, revoked, or from a different Mist
  cloud. Check the **Cloud** setting matches your org.
- **The token may have leaked.** Delete it in Mist (**My Account** → **API
  Tokens**) and create a new one.
