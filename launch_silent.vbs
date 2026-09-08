' Shorts Maker - friendly launcher
' Goals:
'  1) If the app is already running, just open the browser (no restart, no tug-of-war).
'  2) If it's not running, start the engine, show a quick "Starting..." notice so you
'     know to wait instead of clicking again, then open the browser once it's ready.
'  3) If the engine never comes up, show a plain message instead of a broken page.

Option Explicit

Dim sh, fso, here, pyw, url, i, ready

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Folder this script lives in (the Shorts Maker folder)
here = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = here & "\venv\Scripts\pythonw.exe"
url = "http://127.0.0.1:5050"

' --- 1) Already running? Then just open the browser and stop. -------------------
If IsUp(url) Then
    CreateObject("Shell.Application").ShellExecute url
    WScript.Quit
End If

' --- 2) Not running. Clear any stuck leftover holding the port, then start fresh -
sh.Run "powershell -NoProfile -WindowStyle Hidden -Command ""Get-NetTCPConnection -LocalPort 5050 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }""", 0, True

sh.CurrentDirectory = here
sh.Run """" & pyw & """ app.py", 0, False

' Quick, auto-closing notice so you know it's working (don't re-click).
' 64 = info icon. Closes itself after 4 seconds.
sh.Popup "Starting Shorts Maker..." & Chr(10) & Chr(10) & "This takes a few seconds. It will open in your browser automatically.", 4, "Shorts Maker", 64

' --- 3) Wait until the engine actually answers (up to ~30 seconds total) ---------
ready = False
For i = 1 To 60
    If IsUp(url) Then
        ready = True
        Exit For
    End If
    WScript.Sleep 500
Next

If ready Then
    CreateObject("Shell.Application").ShellExecute url
Else
    MsgBox "Shorts Maker could not start this time." & Chr(10) & Chr(10) & _
           "Please wait a moment and try again. If it keeps happening, tell Claude and mention that the engine did not come up.", _
           48, "Shorts Maker"
End If

' --- Helper: is the app answering on the given URL? ------------------------------
Function IsUp(u)
    Dim h
    IsUp = False
    On Error Resume Next
    Set h = CreateObject("MSXML2.XMLHTTP")
    h.Open "GET", u, False
    h.Send
    If Err.Number = 0 And h.Status = 200 Then IsUp = True
    On Error GoTo 0
End Function
