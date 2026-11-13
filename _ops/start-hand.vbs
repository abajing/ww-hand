' start-hand.vbs - hidden elevated launcher for the desktop-automation broker.
'
' Keep this file pure ASCII. wscript.exe reads .vbs in the system ANSI code page,
' so a UTF-8 Chinese comment can corrupt the next line and break the script.
'
' API note (a bug that cost a round):
'   WScript.Shell has NO ShellExecute method. That belongs to Shell.Application.
'   WScript.Shell.ShellExecute -> "Object doesn't support this property or
'   method: 'ShellExecute'" (0x800A01B6). Use Shell.Application for runas.
'
' Which interpreter:
'   If _ops\python.txt exists (one line: full path to python.exe), we take the
'   pythonw.exe sitting next to it. pythonw is the GUI-subsystem build, so the
'   guard and worker never flash a console window. Without that file we fall
'   back to "pythonw.exe" from PATH.
'
' Elevation:
'   The guard must be elevated, because Windows blocks a lower-integrity process
'   from posting input to a higher-integrity window (UIPI). Launching twice is
'   harmless: the guard holds a named mutex and the extra instance exits.

Dim fso, here, pyw, cfg, py, cand
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))

pyw = "pythonw.exe"
cfg = here & "\_ops\python.txt"
If fso.FileExists(cfg) Then
  Dim ts
  Set ts = fso.OpenTextFile(cfg, 1)
  If Not ts.AtEndOfStream Then py = Trim(ts.ReadLine)
  ts.Close
  If Len(py) > 0 Then
    cand = fso.BuildPath(fso.GetParentFolderName(py), "pythonw.exe")
    If fso.FileExists(cand) Then pyw = cand
  End If
End If

Dim guard
guard = here & "\_ops\worker_guard.py"
If Not fso.FileExists(guard) Then
  MsgBox "worker_guard.py not found:" & vbCrLf & guard, 16, "WW-HAND"
  WScript.Quit 1
End If

Dim sh
Set sh = CreateObject("Shell.Application")
sh.ShellExecute pyw, """" & guard & """", here & "\_ops", "runas", 0
