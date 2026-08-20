"""app.has_modern_webview() — the guard that keeps the board out of IE11.

Why this test exists
--------------------
On a clean Windows machine with no Edge WebView2 runtime, pywebview does not
fail. It silently selects MSHTML (the IE11 engine) and reports success, and the
board renders as unstyled default HTML — blank job canvas, native select boxes,
overlapping controls. The 2026-08-16 sandbox QA run shipped exactly that with a
PASS verdict, because every scripted check was an HTTP status and none of them
could see a rendered page.

So the decision "is there a web view that can actually render this?" is now a
pure function over the registry, and this is where it is pinned. Getting it
wrong in the permissive direction is the expensive one: it puts the unstyled
board in front of a stranger.

Runs on any OS: the platform and winreg are both injected, so nothing here
touches a real registry.
"""
import sys
import types

import pytest

import app


# --- a registry we control ---------------------------------------------------
# app.has_modern_webview() does `import winreg` INSIDE the function, so putting a
# fake in sys.modules is enough — no import-order games, and on Linux/macOS this
# is what makes the Windows branch reachable at all.

HKLM, HKCU = 0, 1
KEY_WOW, KEY_PLAIN = app._WEBVIEW2_KEYS


class _FakeKey:
    def __init__(self, values):
        self.values = values

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_winreg(table):
    """table maps (hive, subkey) -> {value_name: value}. Anything absent raises."""
    mod = types.ModuleType('winreg')
    mod.HKEY_LOCAL_MACHINE, mod.HKEY_CURRENT_USER = HKLM, HKCU

    def OpenKey(hive, key):
        if (hive, key) not in table:
            raise OSError(2, 'The system cannot find the file specified')
        return _FakeKey(table[(hive, key)])

    def QueryValueEx(key, name):
        if name not in key.values:
            raise OSError(2, 'The system cannot find the file specified')
        return key.values[name], 1

    mod.OpenKey, mod.QueryValueEx = OpenKey, QueryValueEx
    return mod


@pytest.fixture
def on_windows(monkeypatch):
    """Pretend to be Windows, with a registry the test supplies."""
    monkeypatch.setattr(app.sys, 'platform', 'win32', raising=False)

    def install(table):
        monkeypatch.setitem(sys.modules, 'winreg', _fake_winreg(table))
    return install


# --- absent runtime ----------------------------------------------------------

def test_clean_windows_has_no_webview(on_windows):
    """The Windows Sandbox case, and the one that caused the bad release."""
    on_windows({})
    assert app.has_modern_webview() is False


def test_placeholder_version_is_not_an_install(on_windows):
    """EdgeUpdate registers pv=0.0.0.0 for a client it knows about but has not installed."""
    on_windows({(HKLM, KEY_WOW): {'pv': '0.0.0.0'}})
    assert app.has_modern_webview() is False


def test_blank_version_is_not_an_install(on_windows):
    on_windows({(HKLM, KEY_WOW): {'pv': '   '}})
    assert app.has_modern_webview() is False


def test_key_without_pv_value_is_not_an_install(on_windows):
    on_windows({(HKLM, KEY_WOW): {}})
    assert app.has_modern_webview() is False


# --- present runtime ---------------------------------------------------------

def test_machine_wide_install_under_wow6432node(on_windows):
    """Where a machine-wide install lands on x64: the updater is 32-bit."""
    on_windows({(HKLM, KEY_WOW): {'pv': '120.0.2210.91'}})
    assert app.has_modern_webview() is True


def test_per_user_install_under_hkcu(on_windows):
    """Per-user installs write the un-prefixed path, and miss HKLM entirely."""
    on_windows({(HKCU, KEY_PLAIN): {'pv': '121.0.1'}})
    assert app.has_modern_webview() is True


def test_real_install_wins_over_a_placeholder_in_another_hive(on_windows):
    """A stale HKLM placeholder must not mask a genuine per-user install."""
    on_windows({(HKLM, KEY_WOW): {'pv': '0.0.0.0'},
                (HKCU, KEY_PLAIN): {'pv': '120.1'}})
    assert app.has_modern_webview() is True


# --- the other platforms -----------------------------------------------------

def test_non_windows_never_blocks_the_window(monkeypatch):
    """GTK/Cocoa have no MSHTML trap: pywebview finds a WebKit or raises, and the
    raise is already handled by open_in_window(). Probing there would only be a
    way to lose a window we could have had."""
    for plat in ('linux', 'darwin'):
        monkeypatch.setattr(app.sys, 'platform', plat, raising=False)
        assert app.has_modern_webview() is True


def test_unreadable_registry_counts_as_absent(on_windows, monkeypatch):
    """Locked-down box, redirected hive, anything unexpected — fail toward the
    browser. Being wrong that way costs a tab; the other way ships IE11."""
    broken = types.ModuleType('winreg')
    broken.HKEY_LOCAL_MACHINE, broken.HKEY_CURRENT_USER = HKLM, HKCU

    def boom(*a, **k):
        raise PermissionError('Access is denied')

    broken.OpenKey, broken.QueryValueEx = boom, boom
    monkeypatch.setattr(app.sys, 'platform', 'win32', raising=False)
    monkeypatch.setitem(sys.modules, 'winreg', broken)
    assert app.has_modern_webview() is False


# --- what open_in_window() does with the answer ------------------------------
#
# The probe being right is only half of it. These pin the WIRING: that a missing
# runtime short-circuits BEFORE a window exists, and that the present case still
# names the backend so pywebview can never silently choose MSHTML.

class _SpyWebview(types.ModuleType):
    def __init__(self):
        super().__init__('webview')
        self.created, self.started = [], []

    def create_window(self, *a, **k):
        self.created.append((a, k))

    def start(self, *a, **k):
        self.started.append((a, k))


@pytest.fixture
def spy_webview(monkeypatch):
    spy = _SpyWebview()
    monkeypatch.setitem(sys.modules, 'webview', spy)
    monkeypatch.setattr(app.sys, 'platform', 'win32', raising=False)
    return spy


def test_no_webview2_falls_back_without_creating_a_window(spy_webview, monkeypatch, capsys):
    """The regression that shipped: a window was created and TRUE returned, so
    main() never reached open_in_browser() and the user got the IE11 board."""
    monkeypatch.setattr(app, 'has_modern_webview', lambda: False)

    assert app.open_in_window('http://127.0.0.1:8765/') is False
    assert spy_webview.created == [], 'a window was built before the guard ran'
    assert spy_webview.started == []
    assert 'browser' in capsys.readouterr().out.lower(), 'the user was told nothing'


def test_webview2_present_names_the_backend(spy_webview, monkeypatch):
    """gui='edgechromium' is what makes a wrong probe raise instead of quietly
    rendering in MSHTML."""
    monkeypatch.setattr(app, 'has_modern_webview', lambda: True)
    monkeypatch.setattr(app, 'icon_path', lambda: '')

    assert app.open_in_window('http://127.0.0.1:8765/') is True
    assert len(spy_webview.created) == 1
    assert spy_webview.started[0][1].get('gui') == 'edgechromium'


def test_backend_is_not_named_off_windows(spy_webview, monkeypatch):
    """'edgechromium' is a Windows backend; passing it on GTK/Cocoa would turn a
    working window into an exception."""
    monkeypatch.setattr(app.sys, 'platform', 'linux', raising=False)
    monkeypatch.setattr(app, 'has_modern_webview', lambda: True)
    monkeypatch.setattr(app, 'icon_path', lambda: '')

    assert app.open_in_window('http://127.0.0.1:8765/') is True
    assert 'gui' not in spy_webview.started[0][1]
