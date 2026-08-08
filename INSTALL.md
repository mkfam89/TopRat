# Install Top Rat

Top Rat finds job postings, scores them against your skills, tailors a resume for the
good ones, and shows everything on a board you open on your own computer.

It runs **entirely on your machine**. There is no account, no server, no sign-up. Your
resume and your job list never leave your computer.

Installing takes about five minutes.

---

## Before you start

You need **Python 3.8 or newer**. That is the only requirement.

**Check whether you already have it.**

- **Windows** — press `Win + R`, type `cmd`, press Enter, then type:

  ```
  python --version
  ```

- **macOS** — open Terminal (press `Cmd + Space`, type "Terminal", press Enter), then type:

  ```
  python3 --version
  ```

If you see something like `Python 3.11.4`, you are ready. Skip to Step 1.

If you see an error, or a version starting with `2.`, install Python from
<https://www.python.org/downloads/>.

> **Windows users — this part matters.** On the first screen of the Python installer,
> tick the box that says **"Add python.exe to PATH"** before clicking Install. It is easy
> to miss, and without it Top Rat cannot find Python.

---

## Step 1 — Download

Download the project as a ZIP file and unzip it somewhere you will find again, such as
your Documents folder.

Top Rat keeps all your data inside its own folder, so put it somewhere permanent — not
in Downloads, where you might clear it out later.

---

## Step 2 — Start it

**Windows:** double-click **`Start Here.bat`**

**macOS and Linux:** double-click **`Start Here.command`**

A small black window opens and reports what it is doing. After a few seconds your board
appears. That black window can be closed once the board is up.

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
Python is probably not installed, or not on your PATH. Re-read "Before you start". On
Windows, re-run the Python installer and tick "Add python.exe to PATH".

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
