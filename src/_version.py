"""_version.py — the single place the app's version number lives.

Why it exists
-------------
Support conversations start with "what version are you on?". Without a version
string, the answer is "the one I downloaded", which is not an answer. The About
row on the Setup page reads this, so a user can read it back without opening a
file.

Bump `VERSION` when you publish a release. Nothing here imports anything, so it
is safe for any module (or a packaging script) to read.
"""

VERSION = "1.2.2"

# Human name of the app, used in window titles and the About row. Kept next to
# the version so branding is not scattered across the UI files.
APP_NAME = "Top Rat"


def version_string():
    """'Top Rat 1.2.2' — what the About row and the desktop window title show."""
    return f"{APP_NAME} {VERSION}"
