# src/ops — install, keep alive, back up

Housekeeping. You run these rarely, and mostly through the `.bat` files in the project
root or the dashboard's Settings page.

| File | What it does |
|------|--------------|
| `setup.py` | First-run wizard: builds your profile and the config derived from it. |
| `watchdog.py` | Checks the dashboard is running; starts it minimized if not. |
| `stop_board.py` | Stops the dashboard **and** pauses the watchdog so it stays stopped. Behind `tools\Stop Board.bat`. `--list` reports without killing. |
| `make_watchdog.py` | Registers/removes the Windows task that runs the watchdog. Behind `Install Watchdog.bat`. |
| `make_autostart.py` | Legacy Startup-folder launcher, superseded by the watchdog. Kept so old installs can be turned off. |
| `git_daily.py` | Commits code + tracking metadata to a per-day branch and pushes. |
| `backup.py` | Snapshots `src/`, config, and tracker state to `Backups/snapshots/`, keeping the newest 5. |
| `archive.py` | Monthly: moves applied jobs older than a month out of the active board. |
| `cleanup_user_data.py` | One-shot migration helper: deletes personal files left in the code folder, but only after proving the data folder has an identical copy. |
| `diag_hiringcafe.py` | Throwaway diagnostic for scraper breakage. Safe to delete. |
