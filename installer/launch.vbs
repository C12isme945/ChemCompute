Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
exe = fs.BuildPath(fs.GetParentFolderName(WScript.ScriptFullName), "ChemCompute.exe")
shell.Run Chr(34) & exe & Chr(34) & " desktop-run", 0, False
