"""Reject competing physical controllers, including gaps between their windows."""
import json
import os
import subprocess

def reject_foreign_controllers():
    if os.name=='nt':
        command="@(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python' -and $_.CommandLine -match 'run_v3_[A-Za-z_]+[.]py|run_adaptive_phases[.]py|run_adaptive_sweep[.]py|pipeline[/\\\\]monitor[.]py' } | Select-Object -ExpandProperty ProcessId) | ConvertTo-Json -Compress"
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],check=True,capture_output=True,text=True,timeout=15)
        ids=json.loads(result.stdout or '[]')
        ids=ids if isinstance(ids,list) else [ids]
    else:
        result=subprocess.run(['ps','-eo','pid,args'],check=True,capture_output=True,text=True,timeout=10)
        ids=[int(row.split()[0]) for row in result.stdout.splitlines() if any(s in row for s in ('run_v3_','run_adaptive_phases.py','pipeline/monitor.py'))]
    if any(pid!=os.getpid() for pid in ids):
        raise RuntimeError('another physical controller or graph monitor is active')
