' ==============================================================================
' run-hidden.vbs
' Launches a command line in the background with SW_HIDE (0) so no console window
' pops up, flashes, or steals focus from the user's active session.
' ==============================================================================
Set shell = CreateObject("WScript.Shell")
Dim cmd, i, arg
cmd = ""
For i = 0 To WScript.Arguments.Count - 1
    arg = WScript.Arguments(i)
    If InStr(arg, " ") > 0 Or InStr(arg, "&") > 0 Then
        arg = """" & arg & """"
    End If
    If i = 0 Then
        cmd = arg
    Else
        cmd = cmd & " " & arg
    End If
Next
Dim exitCode
exitCode = shell.Run(cmd, 0, True)
WScript.Quit exitCode
