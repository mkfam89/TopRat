# Install Top Rat

Top Rat finds job postings, scores them against your skills, tailors a resume for the
good ones, and shows everything on a board you open on your own computer.

It runs **entirely on your machine**. There is no account, no server, no sign-up. Your
resume and your job list never leave your computer.

Installing takes about five minutes.

---

## Step 1 — Download

Go to the **Releases** page and download the file for your computer:

| Your computer | Download |
|---|---|
| Windows | `TopRat-<version>-windows-x64.zip` |
| Mac (Apple silicon — M1 and later) | `TopRat-<version>-macos-arm64.zip` |
| Mac (Intel) | `TopRat-<version>-macos-x64.zip` |

Not sure which Mac you have? Click the Apple menu → **About This Mac**. If the Chip line
says "Apple M-something", take the arm64 file.

**These downloads include their own copy of Python, so you do not need to install
anything else.** It is a private copy that lives inside the Top Rat folder, changes
nothing on the rest of your computer, and is deleted with the folder.

Unzip it somewhere permanent, such as your Documents folder. Top Rat keeps all your data
inside its own folder, so avoid Downloads, where you might clear it out later.

> **Only if you were given a source copy instead** (a `.zip` named `Source code`, or a
> git clone): that version has no Python inside it, so you need **Python 3.8 or newer**
> installed. Check with `python --version` (Windows) or `python3 --version` (macOS). If
> it is missing, get it from <https://www.python.org/downloads/> — and on Windows, tick
> **"Add python.exe to PATH"** on the installer's first screen. It is easy to miss, and
> without it Top Rat cannot find Python.

---

## Step 2 — Start it

**Windows:** double-click **`Start Here.bat`**

**macOS and Linux:** double-click **`Start Here.command`**

A small black window opens and reports what it is doing. After a few seconds your board
appears in its own window. That black window can be closed once the board is up.

If you are using your own Python rather than the bundled copy, the launcher asks you once
whether to set up the app window (a small download). Say no and the board opens in your
normal browser instead — the same app, just a tab. You are not asked again; to change your
mind, delete `config/app_window.answered` and start it again.

That is the whole install. Everything below is optional.

---

## If your computer warns you

The app is not code-signed, because a signing certificate costs a few hundred dollars a
year. Your computer therefore does not recognise the publisher and will say so once. This
is expected, and after you allow it the first time you will not be asked again.

**macOS — "cannot be opened because it is from an unidentified developer"**

1. Right-click (or Control-click) `Start Here.command`
2. Choose **Open**
3. Click **Open** in the dialog

The right-click matters: the plain double-click gives you no "Open anyway" button. If
macOS still refuses, open Terminal in the project folder and run:

```
xattr -d com.apple.quarantine "Start Here.command"
chmod +x "Start Here.command"
```

**Windows — "Windows protected your PC"**

1. Click **More info**
2. Click **Run anyway**

---

## Step 3 — Set up your profile

The first time the board opens, go to the **Setup** page and fill in:

- Your name and where you want to work
- The job titles you are looking for
- Your resume — put your base resume in the `resume_template` folder

The Setup page walks you through the rest. Nothing there is required to get started; the
sections you skip simply stay switched off.

---

## Optional extras

**If you downloaded a Release zip, skip this section** — the app window and resume
writing are already included. This is for source copies.

Top Rat works without every one of these. Each unlocks one more feature, and the app
tells you when something is missing rather than failing.

Install them all at once by opening a terminal in the project folder and running:

```
pip install -r requirements.txt
```

Or pick individually:

| Install this | What it gives you | Without it |
|---|---|---|
| `pip install pywebview` | Its own app window | Opens in your normal browser |
| `pip install python-docx` | Tailored `.docx` resumes | Jobs are still found and scored |
| `pip install python-jobspy` | LinkedIn as a job source | Other job sources still work |
| [LibreOffice](https://www.libreoffice.org/download/) | A `.pdf` next to each `.docx` | You get the `.docx` only |

LibreOffice is a normal application, not a Python package. Install it only if you want PDF
copies for job sites that ask for one.

---

## Keeping it running

By default Top Rat runs only while it is open, and scheduled searches happen only during
that time.

To have it start by itself, open the **Setup** page and turn on **"Keep it running"**. Then
you never need the Start Here file again.

---

## If something goes wrong

**Nothing happens when I double-click.**
If you downloaded a Release zip, check there is a `python` folder next to `Start Here.bat`
— if it is missing, the unzip did not finish, so unzip it again. If you are running from a
source copy, Python is probably not installed or not on your PATH; on Windows, re-run the
Python installer and tick "Add python.exe to PATH".

**The window flashes and disappears.**
It should never do this — every error path is supposed to stop and tell you why. If it
does, open a terminal in the project folder and run `python app.py` to see the message.

**The board does not open, or the page will not load.**
Something else may be using the port. Top Rat picks another one automatically, so try
starting it again first. If it still fails, open `logs/execution.log` and read the last
few lines — they say what failed.

**It says the dashboard is already running.**
It is. Open <http://127.0.0.1:8765/> in your browser.

**How do I stop it completely?**

```
python app.py --stop-info
```

That prints the steps for your system.

---

## Uninstalling

Delete the folder. That is all of it.

Top Rat does not write to your registry, does not install system-wide packages, and does
not put files anywhere else — with one exception: if you turned on "Keep it running", turn
that off on the Setup page first (Windows users can also run `Uninstall Watchdog.bat`), so
nothing tries to start it again after the folder is gone.
