# src/ops — installation, watchdog, backup

These scripts do the maintenance tasks. You run them rarely. Usually you run them from the
`.bat` files in the project root, or from the Settings page of the dashboard.

| File | What it does |
|------|--------------|
| `setup.py` | The first-run wizard. It builds your profile and the configuration that comes from that profile. |
| `watchdog.py` | Makes sure that the dashboard runs. If the dashboard is closed, the script starts it minimized. |
| `stop_board.py` | Stops the dashboard **and** pauses the watchdog, so the dashboard stays closed. `tools\Stop Board.bat` calls it. `--list` shows the processes but stops nothing. |
| `make_watchdog.py` | Adds or removes the Windows task that runs the watchdog. `Install Watchdog.bat` calls it. |
| `make_autostart.py` | The old Startup-folder launcher. The watchdog replaces it. It stays here so that you can turn off an old installation. |
| `git_daily.py` | Commits the code and the tracking data to a branch for that day. Then it pushes the branch. |
| `backup.py` | Copies `src/`, the configuration and the tracker state to `Backups/snapshots/`. It keeps the newest 5 copies. |
| `archive.py` | Runs each month. It moves applied jobs more than one month old off the active board. |
| `cleanup_user_data.py` | A migration tool that you run one time. It deletes personal files from the code folder, but only after it finds an identical copy in the data folder. |
| `diag_hiringcafe.py` | A diagnostic tool for a broken scraper. You can delete it. |
