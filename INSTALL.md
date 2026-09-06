# Install Top Rat

Top Rat finds job postings. It scores each posting against your skills. It writes a
tailored resume for the good ones. It shows the result on a board that you open on your
own computer.

The app runs only on your machine. It has no account, no server and no sign-up. Your
resume and your job list never leave your computer.

The installation takes about five minutes.

---

## Step 1 — Download

Go to the **Releases** page. Download the file for your computer:

| Your computer | Download |
|---|---|
| Windows | `TopRat-<version>-windows-x64.zip` |
| Mac (Apple silicon — M1 and later) | `TopRat-<version>-macos-arm64.zip` |
| Mac (Intel) | `TopRat-<version>-macos-x64.zip` |

To identify your Mac, click the Apple menu. Then click **About This Mac**. If the Chip
line shows "Apple M" and a number, take the arm64 file.

**These downloads include their own copy of Python and every package Top Rat needs. You do
not need to install anything else, and the app never downloads anything the first time you
open it.** This copy is private. It stays inside the Top Rat folder, changes nothing else
on your computer, and goes away with the folder.

There is **one** optional extra, on the same Releases page:

| Add-on | What it gives you | Size |
|---|---|---|
| `TopRat-<version>-linkedin-<your platform>.zip` | LinkedIn as a job source | about 270 MB |

It is separate because it is five times the size of the app, for one job source among
several. Take it only if you want LinkedIn. To install it, unzip it **into your Top Rat
folder** — the same folder that holds `Start Here` — and answer yes to merging the `python`
folder. Nothing else changes. Without it the app says LinkedIn is off and every other job
source works as normal. The add-on has to match your platform, the same way the main
download does.

Unzip the file into a permanent location, such as your Documents folder. Top Rat keeps all
your data inside its own folder. Do not use the Downloads folder, because you can empty it
later.

> **Read this only if you got a source copy** (a `.zip` with the name `Source code`, or a
> git clone). A source copy has no Python inside it. You must install **Python 3.8 or
> newer**. To see the version, run `python --version` on Windows or `python3 --version` on
> macOS. If Python is absent, get it from <https://www.python.org/downloads/>. On Windows,
> select **"Add python.exe to PATH"** on the first screen of the installer. This box is
> easy to miss, and without it Top Rat cannot find Python.

---

## Step 2 — Start the app

**Windows:** double-click **`Start Here.bat`**

**macOS and Linux:** double-click **`Start Here.command`**

A small black window opens and shows each step. After a few seconds, your board opens in
its own window. You can then close the black window.

If you use your own Python, the launcher downloads the app window package for you the
first time you start. This takes a minute. If the download does not work, the board
opens in your usual browser instead. It is the same app, in a tab. The launcher tries
only once. To make it try again, delete `config/app_window.answered` and start the app
again.

The installation is complete. Everything below is optional.

---

## If your computer gives you a warning

The app has no code signature, because a signing certificate costs a few hundred dollars
each year. Your computer therefore does not know the publisher, and it tells you once.
This warning is normal. After you permit the app one time, the warning stops.

**macOS — "cannot be opened because it is from an unidentified developer"**

1. Right-click (or Control-click) `Start Here.command`
2. Click **Open**
3. Click **Open** in the dialog

Use the right-click. A normal double-click gives you no "Open anyway" button.

Recent versions of macOS word this differently and put the button somewhere else. If the
message says that macOS "could not verify" the file, or the dialog offers you only **Done**,
then open **System Settings > Privacy & Security**, scroll to the Security section, and
click **Open Anyway** next to the name of the file. macOS asks you once for each file.

If macOS still refuses, open Terminal in the project folder. Then run these two commands:

```
xattr -d com.apple.quarantine "Start Here.command"
chmod +x "Start Here.command"
```

**Windows — "Windows protected your PC"**

1. Click **More info**
2. Click **Run anyway**

---

## Step 3 — Make your profile

The first time the board opens, go to the **Setup** page. Fill in these items:

- Your name and the place where you want to work
- The job titles that you look for
- Your resume. Put your base resume in the `resume_template` folder

The Setup page takes you through the other items. No item is necessary for the first run.
The sections that you skip stay off.

---

## Optional extras

**If you downloaded a Release zip, skip this section.** That zip already includes the app
window, the resume writer and the config checker; LinkedIn is the separate add-on described
in Step 1. This section is for source copies.

Top Rat runs without each one of these packages. Each package adds one more feature. If a
package is absent, the app tells you and continues.

To install them all at the same time, open a terminal in the project folder. Then run this
command:

```
pip install -r requirements.txt
```

You can also install them one at a time:

| Install this | What it gives you | Without it |
|---|---|---|
| `pip install pywebview` | Its own app window | The board opens in your usual browser |
| `pip install python-docx` | Tailored `.docx` resumes | The app still finds and scores jobs |
| `pip install python-jobspy` | LinkedIn as a job source | The other job sources still work |
| [LibreOffice](https://www.libreoffice.org/download/) | A `.pdf` beside each `.docx` | You get the `.docx` only |

LibreOffice is a normal application, not a Python package. Install it only if you want PDF
copies for job sites that ask for one.

---

## Keep the app running

By default, Top Rat runs only while it is open. The scheduled searches happen only in that
time.

To start the app automatically, go to the **Setup** page. Then turn on **"Keep it
running"**. After that, you do not need the Start Here file again.

This works on Windows and on macOS. Top Rat asks your own computer to look at the dashboard
every few minutes. If the dashboard is not running, your computer starts it again. Windows
does this with a scheduled task, and it puts the window in the background, minimized. macOS
does this with a LaunchAgent in your own `~/Library/LaunchAgents` folder, and it opens the
board in your browser in front of what you are doing, because macOS gives no way to open a
web page minimized. On a Mac, turn this off or use Pause while you need the screen.
Neither one needs an administrator password, and neither one runs while you are signed out.

You can also do it from a file instead of the Setup page:

| Your computer | Turn it on | Turn it off |
|---|---|---|
| Windows | `Install Watchdog.bat` | `Uninstall Watchdog.bat` |
| macOS and Linux | `Install Watchdog.command` | `Uninstall Watchdog.command` |

If macOS does not let you double-click the `.command` file, use the same two commands that
Step 2 gives for `Start Here.command`: `xattr -d com.apple.quarantine` and `chmod +x`.

---

## If something goes wrong

**Nothing happens when I double-click.**
If you downloaded a Release zip, look for a `python` folder beside the launcher
(`Start Here.bat` on Windows, `Start Here.command` on macOS). If
that folder is absent, the unzip did not finish. Unzip the file again. If you run a source
copy, Python is absent or not on your PATH. On Windows, run the Python installer again and
select "Add python.exe to PATH".

**The window flashes and goes away.**
The app must not do this. Every error path stops and gives a reason. If the window still
goes away, open a terminal in the project folder. Then run `python app.py` to see the
message.

**The board does not open, or the page does not load.**
Another program can hold the port. Top Rat then picks a different port, so start the app
again first. If the board still does not open, read the last lines of
`logs/execution.log`. Those lines name the failure.

**The app says that the dashboard already runs.**
It does. Open <http://127.0.0.1:8765/> in your browser.

**How do I stop the app completely?**

```
python app.py --stop-info
```

That command prints the steps for your system.

---

## Uninstall

Delete the folder. That step removes all of it.

Top Rat does not write to your registry. It does not install system-wide packages. It puts
no files anywhere else. There is one exception. If you turned on "Keep it running", turn
that setting off on the Setup page first. You can also run `Uninstall Watchdog.bat` on
Windows, or `Uninstall Watchdog.command` on macOS. Then nothing tries to start the app after
you delete the folder.

That setting is the only thing Top Rat puts outside its folder: one scheduled task named
`TopRatWatchdog` on Windows, or one file named `com.toprat.watchdog.plist` in your
`~/Library/LaunchAgents` folder on macOS. Turning the setting off removes it.
