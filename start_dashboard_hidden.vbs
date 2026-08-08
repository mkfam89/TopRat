' Starts the Top Rat dashboard server hidden (no console window, no browser popup).
' Double-click to run it silently in the background. It runs from its own folder, so the server
' reads that copy's config/instance.json and binds the right port (live 8765 / staging 8766).
Set WShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
proj = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
WShell.CurrentDirectory = proj

' Prefer the BUNDLED interpreter (python\ beside this file) so a shipped copy works on a
' machine with no Python installed; fall back to whatever is on PATH. pythonw.exe has no
' console attached, which is what keeps this launch silent.
py = "pythonw"
If FSO.FileExists(proj & "python\pythonw.exe") Then
  py = """" & proj & "python\pythonw.exe"""
End If
WShell.Run py & " """ & proj & "src\web\dashboard_server.py"" --no-browser", 0, False
