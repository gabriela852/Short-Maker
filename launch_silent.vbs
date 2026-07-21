' Shorts Maker - silent launcher
' Starts the app engine with NO black terminal window, waits until it's ready,
' then opens the app in your default browser. Nothing visible except the browser.

Dim sh, fso, here, pyw, url
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Folder this script lives in (the Shorts Maker folder)
here = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = here & "\venv\Scripts\pythonw.exe"
url = "http://127.0.0.1:5050"

' 1) Clear out any leftover copy still holding the port (hidden PowerShell, wait for it)
sh.Run "powershell -NoProfile -WindowStyle Hidden -Command ""Get-NetTCPConnection -LocalPort 5050 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }""", 0, True

' 2) Start the engine with NO console window (pythonw), from the app folder
sh.CurrentDirectory = here
sh.Run """" & pyw & """ app.py", 0, False

' 3) Wait until the engine actually answers (up to ~20 seconds), then open the browser
Dim http, i, ready
ready = False
Set http = CreateObject("MSXML2.XMLHTTP")
For i = 1 To 60
    On Error Resume Next
    http.Open "GET", url, False
    http.Send
    If Err.Number = 0 And http.Status = 200 Then ready = True
    On Error GoTo 0
    If ready Then Exit For
    WScript.Sleep 500
Next

' 4) Open the app in the default browser (no console flash)
CreateObject("Shell.Application").ShellExecute url
