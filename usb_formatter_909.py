"""
usb_formatter.py

"""
import os
import sys
import ctypes
import subprocess
import tempfile
import tkinter as tk
from tkinter import messagebox, simpledialog


DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def get_drive_type(letter):
    root = f"{letter}:\\"
    return ctypes.windll.kernel32.GetDriveTypeW(root)


def get_volume_info(letter):
    root = f"{letter}:\\"
    buf = ctypes.create_unicode_buffer(1024)
    fsbuf = ctypes.create_unicode_buffer(1024)
    serial = ctypes.c_ulong()
    maxfile = ctypes.c_ulong()
    flags = ctypes.c_ulong()
    try:
        res = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root),
            buf,
            ctypes.sizeof(buf),
            ctypes.byref(serial),
            ctypes.byref(maxfile),
            ctypes.byref(flags),
            fsbuf,
            ctypes.sizeof(fsbuf),
        )
        if res:
            return buf.value, fsbuf.value
    except Exception:
        pass
    return "", ""


def run_proc(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, shell=True)
        return p.returncode, p.stdout + p.stderr
    except Exception as e:
        return 1, str(e)


def run_powershell_file(script_text):
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.ps1') as f:
        f.write(script_text)
        path = f.name
    cmd = f'powershell -NoProfile -ExecutionPolicy Bypass -File "{path}"'
    rc, out = run_proc(cmd)
    try:
        os.remove(path)
    except Exception:
        pass
    return rc, out


def run_diskpart_script(lines):
    """Run a DiskPart script (lines is list of commands). Returns (rc, output)."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        for l in lines:
            f.write(l + '\n')
        path = f.name
    cmd = f'diskpart /s "{path}"'
    rc, out = run_proc(cmd)
    try:
        os.remove(path)
    except Exception:
        pass
    return rc, out


def relaunch_as_admin():
    """Relaunch the current script with elevation using ShellExecute 'runas'."""
    if os.name != 'nt':
        return False
    python = sys.executable
    script = os.path.abspath(sys.argv[0])
    params = ' '.join([f'"{arg}"' for arg in sys.argv[1:]])
    try:
        ctypes.windll.shell32.ShellExecuteW(None, 'runas', python, f'"{script}" {params}', None, 1)
        return True
    except Exception as e:
        print('Elevation failed:', e)
        return False


def list_removable_drives():
    """Return list of drives (letter or DISK#) with metadata.

    Each entry is a tuple:
      (letter_or_disk, volume_name, fs, size_bytes, diskindex, model, health)
    """
    MAX_BYTES = 256 * 1024 * 1024 * 1024  # 256 GiB
    drives = []
    ps_script = r"""
$usb = Get-WmiObject Win32_DiskDrive -Filter "InterfaceType='USB'"
foreach ($d in $usb) {
  $index = $d.Index
  $size = $d.Size
  $model = ($d.Model -replace '\|',' ')
  $parts = Get-WmiObject Win32_DiskDriveToDiskPartition | Where-Object { $_.Antecedent -like "*" + $d.DeviceID + "*" }
  if ($parts) {
    foreach ($p in $parts) {
      $partid = ($p.Dependent -replace '.*DeviceID="(.*)".*','$1')
      $lds = Get-WmiObject Win32_LogicalDiskToPartition | Where-Object { $_.Antecedent -like "*" + $partid + "*" }
      if ($lds) {
        foreach ($ld in $lds) {
          $drive = ($ld.Dependent -replace '.*DeviceID="(.*)".*','$1')
          $vol = Get-WmiObject Win32_LogicalDisk -Filter "DeviceID='$drive'"
          $fs = ''
          $vn = ''
          if ($vol) { $fs = $vol.FileSystem; $vn = $vol.VolumeName }
          $health = ''
          try { $health = (Get-Volume -DriveLetter $drive.TrimEnd(':') -ErrorAction SilentlyContinue).HealthStatus } catch { $health = '' }
          Write-Output "$($drive)|$($vn)|$($fs)|$size|$index|$model|$health"
        }
      } else {
        Write-Output "| | |$size|$index|$model|"
      }
    }
  } else {
    Write-Output "| | |$size|$index|$model|"
  }
}
"""
    rc, out = run_powershell_file(ps_script)
    if rc == 0 and out.strip():
        for line in out.splitlines():
            parts = line.split('|')
            if len(parts) < 7:
                continue
            drive, label, fs, size_s, diskindex, model, health = parts
            try:
                size = int(size_s) if size_s and size_s.isdigit() else 0
            except Exception:
                size = 0
            if size and size > MAX_BYTES:
                continue
            if drive and drive.strip():
                letter = drive.strip()[0]
                drives.append((letter, label or "", fs or "", size, diskindex, model or "", health or ""))
            else:
                drives.append((f"DISK{diskindex}", label or "", fs or "", size, diskindex, model or "", health or ""))

    # fallback: scan mounted letters
    if not drives:
        for i in range(65, 91):
            letter = chr(i)
            try:
                t = get_drive_type(letter)
            except Exception:
                continue
            if t in (DRIVE_REMOVABLE, DRIVE_FIXED):
                path = f"{letter}:\\"
                if os.path.exists(path):
                    label, fs = get_volume_info(letter)
                    size = 0
                    try:
                        rc2, out2 = run_proc(f"powershell -NoProfile -Command \"(Get-Volume -DriveLetter {letter}).Size\"")
                        if rc2 == 0 and out2.strip().isdigit():
                            size = int(out2.strip())
                    except Exception:
                        size = 0
                    if size and size > MAX_BYTES:
                        continue
                    drives.append((letter, label or "", fs or "", size, '', '', ''))
    return drives


# UI globals
root = None
listbox = None
output = None


def log(text):
    if output:
        output.insert('end', text + '\n')
        output.see('end')
    else:
        print(text)


def refresh():
    listbox.delete(0, 'end')
    drives = list_removable_drives()
    for d in drives:
        if isinstance(d[0], str) and d[0].startswith('DISK'):
            display = f"{d[0]} - {int(d[3]/1024/1024/1024) if d[3] else 0}GB - {d[5]} - {d[6]}"
        else:
            display = f"{d[0]}:\\ - {d[1]} - {d[2]} - {int(d[3]/1024/1024/1024) if d[3] else 0}GB - {d[5]} - {d[6]}"
        listbox.insert('end', display)
    log('Refreshed drive list')


def get_selected():
    sel = listbox.curselection()
    if not sel:
        messagebox.showinfo('Select drive', 'Please select a drive from the list')
        return None
    item = listbox.get(sel[0])
    token = item.split()[0]
    if token.endswith(':\\'):
        letter = token[0]
        if letter.upper() == 'C':
            messagebox.showerror('Refuse', 'Refusing to operate on system drive C:')
            return None
        return letter.upper()
    return token


def confirm_drive(letter, action):
    prompt = f"Type the drive letter {letter} to confirm {action}:"
    val = simpledialog.askstring('Confirm', prompt)
    return val and val.strip().upper() == letter


def clear_readonly():
    sel = get_selected()
    if not sel:
        return
    # If a disk token like DISK1 is selected, run DiskPart to clear read-only attribute at disk level
    if isinstance(sel, str) and sel.startswith('DISK'):
        diskidx = sel.replace('DISK', '')
        if not is_admin():
            messagebox.showwarning('Administrator required', 'Run as Administrator and try again.')
            return
        if not messagebox.askyesno('Confirm', f'Clear readonly flags on disk {diskidx}? This is non-destructive but may change device state.'):
            return
        lines = [f'select disk {diskidx}', 'attributes disk clear readonly', 'online disk']
        rc, out = run_diskpart_script(lines)
        log(out or f'diskpart returned {rc}')
        return

    # Otherwise treat as drive letter
    letter = sel
    if not is_admin():
        messagebox.showwarning('Administrator required', 'Run as Administrator and try again.')
        return
    if not confirm_drive(letter, 'clearing read-only flags'):
        return
    # Try PowerShell set-disk first, then fallback to diskpart by disk index
    ps = f'Get-Partition -DriveLetter {letter} | Get-Disk | Set-Disk -IsReadOnly $false -ErrorAction SilentlyContinue'
    rc, out = run_powershell_file(ps)
    log(out or f'Clear readonly returned code {rc}')
    # Additionally attempt diskpart clear if still problematic
    try:
        rc2, out2 = run_proc(f'powershell -NoProfile -Command "(Get-Partition -DriveLetter {letter} | Get-Disk).Number"')
        if rc2 == 0 and out2.strip().isdigit():
            diskidx = out2.strip()
            lines = [f'select disk {diskidx}', 'attributes disk clear readonly', 'online disk']
            rc3, out3 = run_diskpart_script(lines)
            log(out3 or f'diskpart clear readonly returned {rc3}')
    except Exception:
        pass


def fix_permissions():
    sel = get_selected()
    if not sel:
        return
    if isinstance(sel, str) and sel.startswith('DISK'):
        messagebox.showinfo('Disk selected', 'Permissions operations require a mounted drive letter.')
        return
    letter = sel
    if not is_admin():
        messagebox.showwarning('Administrator required', 'Run as Administrator and try again.')
        return
    if not confirm_drive(letter, 'taking ownership and granting full control'):
        return
    path = f"{letter}:\\"
    cmd1 = f'takeown /f "{path}" /r /d y'
    rc1, out1 = run_proc(cmd1)
    log(out1)
    cmd2 = f'icacls "{path}" /grant *S-1-5-32-544:F /t /c'
    cmd3 = f'icacls "{path}" /grant %USERNAME%:F /t /c'
    rc2, out2 = run_proc(cmd2)
    rc3, out3 = run_proc(cmd3)
    log(out2)
    log(out3)


def attrib_reset():
    sel = get_selected()
    if not sel:
        return
    if isinstance(sel, str) and sel.startswith('DISK'):
        messagebox.showinfo('Disk selected', 'Attrib reset requires a mounted drive letter.')
        return
    letter = sel
    if not confirm_drive(letter, 'removing read-only/hidden/system attributes from files'):
        return
    cmd = f'attrib -r -s -h /s /d "{letter}:\\*.*"'
    rc, out = run_proc(cmd)
    log(out or f'attrib returned {rc}')


def format_drive(fs, quick=True):
    sel = get_selected()
    if not sel:
        return
    # If disk token selected, perform DiskPart clean/create/format (destructive)
    if isinstance(sel, str) and sel.startswith('DISK'):
        diskidx = sel.replace('DISK', '')
        if not is_admin():
            messagebox.showwarning('Administrator required', 'Run as Administrator and try again.')
            return
        if not messagebox.askyesno('Confirm destructive', f'Clean and format entire disk {diskidx} as {fs}? ALL DATA WILL BE ERASED'):
            return
        lines = [f'select disk {diskidx}', 'clean', 'create partition primary', f'format fs={fs} quick', 'assign', 'exit']
        rc, out = run_diskpart_script(lines)
        log(out or f'diskpart format returned {rc}')
        return

    # Otherwise operate on drive letter (try to escalate to disk-level if necessary)
    letter = sel
    if not is_admin():
        messagebox.showwarning('Administrator required', 'Run as Administrator and try again.')
        return
    if not confirm_drive(letter, f'format to {fs}'):
        return
    # Try PowerShell Format-Volume first
    ps = f'Format-Volume -DriveLetter {letter} -FileSystem {fs} -Force -Confirm:$false'
    rc, out = run_powershell_file(ps)
    if rc == 0:
        log(out or f'Formatted {letter}: to {fs} (PowerShell)')
        return
    # Fallback: find disk index for letter and perform diskpart clean/format if user confirms
    try:
        rc2, out2 = run_proc(f'powershell -NoProfile -Command "(Get-Partition -DriveLetter {letter} | Get-Disk).Number"')
        if rc2 == 0 and out2.strip().isdigit():
            diskidx = out2.strip()
            if messagebox.askyesno('Confirm deeper format', f'PowerShell format failed. Clean and reformat disk {diskidx}? This erases all partitions.'):
                lines = [f'select disk {diskidx}', 'clean', 'create partition primary', f'format fs={fs} quick', 'assign', 'exit']
                rc3, out3 = run_diskpart_script(lines)
                log(out3 or f'diskpart format returned {rc3}')
                return
    except Exception:
        pass
    # Last fallback: legacy format.exe
    if quick:
        cmd = f'echo Y| format {letter}: /FS:{fs} /Q'
    else:
        cmd = f'echo Y| format {letter}: /FS:{fs}'
    rc3, out3 = run_proc(cmd)
    log(out3 or f'format returned {rc3}')


def wipe_and_format():
    sel = get_selected()
    if not sel:
        return
    # Disk-level wipe and format
    if isinstance(sel, str) and sel.startswith('DISK'):
        diskidx = sel.replace('DISK', '')
        if not is_admin():
            messagebox.showwarning('Administrator required', 'Run as Administrator and try again.')
            return
        if not messagebox.askyesno('Confirm destructive', f'Zero first 100MB and format disk {diskidx}? ALL DATA WILL BE ERASED'):
            return
        # Use diskpart clean and format (clean will remove partition table)
        lines = [f'select disk {diskidx}', 'clean', 'create partition primary', 'format fs=ntfs quick', 'assign', 'exit']
        rc, out = run_diskpart_script(lines)
        log(out or f'diskpart wipe+format returned {rc}')
        return

    # For lettered drives, attempt aggressive wipe via disk index
    letter = sel
    if not is_admin():
        messagebox.showwarning('Administrator required', 'Run as Administrator and try again.')
        return
    if not confirm_drive(letter, 'wiping (zeroing first 100MB) and formatting'):
        return
    try:
        rc2, out2 = run_proc(f'powershell -NoProfile -Command "(Get-Partition -DriveLetter {letter} | Get-Disk).Number"')
        if rc2 == 0 and out2.strip().isdigit():
            diskidx = out2.strip()
            lines = [f'select disk {diskidx}', 'clean', 'create partition primary', 'format fs=ntfs quick', 'assign', 'exit']
            rc3, out3 = run_diskpart_script(lines)
            log(out3 or f'diskpart wipe+format returned {rc3}')
            return
    except Exception:
        pass
    # Fallback simple wipe
    try:
        largefile = f"{letter}:\\__wipe_tmp.bin"
        cmd = f'fsutil file createnew "{largefile}" 104857600'
        rc, out = run_proc(cmd)
        log(out)
        try:
            os.remove(largefile)
        except Exception as e:
            log(str(e))
    except Exception as e:
        log('Wipe helper failed: ' + str(e))
    format_drive('NTFS', quick=True)


def build_ui():
    global root, listbox, output
    root = tk.Tk()
    root.title('USB Permission/Formatter (procedural)')

    info = tk.StringVar()
    info.set('Run as Administrator for full functionality')
    tk.Label(root, textvariable=info).pack(fill='x')

    listbox = tk.Listbox(root, width=80, height=10)
    listbox.pack(padx=8, pady=6)

    btn_frame = tk.Frame(root)
    btn_frame.pack(fill='x', padx=8)

    tk.Button(btn_frame, text='Refresh', command=refresh).pack(side='left')
    tk.Button(btn_frame, text='Clear ReadOnly', command=clear_readonly).pack(side='left')
    tk.Button(btn_frame, text='Fix Permissions', command=fix_permissions).pack(side='left')
    tk.Button(btn_frame, text='Attrib Reset', command=attrib_reset).pack(side='left')
    tk.Button(btn_frame, text='Quick Format NTFS', command=lambda: format_drive('NTFS', quick=True)).pack(side='left')
    tk.Button(btn_frame, text='Quick Format FAT32', command=lambda: format_drive('FAT32', quick=True)).pack(side='left')

    tk.Button(root, text='Wipe (zero first 100MB) + Format', command=wipe_and_format).pack(pady=6)

    output = tk.Text(root, height=12)
    output.pack(fill='both', padx=8, pady=6, expand=True)

    refresh()
    return root


def main():
    app = build_ui()
    app.mainloop()


if __name__ == '__main__':
    # Auto-elevate: relaunch as admin if not running elevated
    if not is_admin():
        try:
            relaunch_as_admin()
            sys.exit(0)
        except Exception:
            pass
    main()
"""
usb_formatter.py

Small Tkinter utility to select a removable drive and run permission/fix/format operations.

WARNING: These operations are destructive. The program asks for explicit confirmation
and prevents formatting the system drive, but run at your own risk. Run as Administrator.

Usage: python usb_formatter.py
"""
import os
import sys
import ctypes
import subprocess
import tempfile
import tkinter as tk
from tkinter import messagebox, simpledialog


DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def get_drive_type(letter):
    root = f"{letter}:\\"
    return ctypes.windll.kernel32.GetDriveTypeW(root)


def get_volume_info(letter):
    root = f"{letter}:\\"
    buf = ctypes.create_unicode_buffer(1024)
    fsbuf = ctypes.create_unicode_buffer(1024)
    serial = ctypes.c_ulong()
    maxfile = ctypes.c_ulong()
    flags = ctypes.c_ulong()
    try:
        res = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root),
            buf,
            ctypes.sizeof(buf),
            ctypes.byref(serial),
            ctypes.byref(maxfile),
            ctypes.byref(flags),
            fsbuf,
            ctypes.sizeof(fsbuf),
        )
        if res:
            return buf.value, fsbuf.value
    except Exception:
        pass
    return "", ""


def list_removable_drives():
    # Prefer PowerShell-based USB enumeration which maps physical USB disks to logical drive letters.
    drives = []
    try:
        ps_script = r"""
$usb = Get-WmiObject Win32_DiskDrive -Filter "InterfaceType='USB'"
foreach ($d in $usb) {
  $parts = Get-WmiObject Win32_DiskDriveToDiskPartition | Where-Object { $_.Antecedent -like "*" + $d.DeviceID + "*" }
  foreach ($p in $parts) {
    $partid = ($p.Dependent -replace '.*DeviceID="(.*)".*','$1')
    $lds = Get-WmiObject Win32_LogicalDiskToPartition | Where-Object { $_.Antecedent -like "*" + $partid + "*" }
    foreach ($ld in $lds) {
      $drive = ($ld.Dependent -replace '.*DeviceID="(.*)".*','$1')
      $vol = Get-WmiObject Win32_LogicalDisk -Filter "DeviceID='$drive'"
      if ($vol) { Write-Output "$($vol.DeviceID)|$($vol.VolumeName)|$($vol.FileSystem)" }
    }
  }
}
"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.ps1') as f:
            f.write(ps_script)
            script_path = f.name
        cmd = f'powershell -NoProfile -ExecutionPolicy Bypass -File "{script_path}"'
        rc, out = run_proc(cmd)
        try:
            os.remove(script_path)
        except Exception:
            pass
        if rc == 0 and out.strip():
            for line in out.splitlines():
                try:
                    drive, label, fs = line.split('|')
                    # drive like "E:"
                    letter = drive.strip()[0]
                    drives.append((letter, label or "", fs or ""))
                    continue
                except Exception:
                    continue
    except Exception:
        drives = []

    # Fallback: original detection using GetDriveType (may report some USBs as fixed)
    if not drives:
        for i in range(65, 91):  # A-Z
            letter = chr(i)
            try:
                t = get_drive_type(letter)
            except Exception:
                continue
            if t in (DRIVE_REMOVABLE, DRIVE_FIXED):
                path = f"{letter}:\\"
                if os.path.exists(path):
                    label, fs = get_volume_info(letter)
                    drives.append((letter, label or "", fs or ""))
    return drives


def run_proc(cmd, admin_hint=True):
    """
    usb_formatter.py

    Procedural single-file USB permission/format helper.

    Run as Administrator for full functionality. Destructive operations require confirmation.
    """
    import os
    import ctypes
    import subprocess
    import tempfile
    import tkinter as tk
    from tkinter import messagebox, simpledialog


    DRIVE_REMOVABLE = 2
    DRIVE_FIXED = 3


    def is_admin():
        try:
            return ctypes.windll.shell32.IsUserAnAdmin()
        except Exception:
            return False


    def get_drive_type(letter):
        root = f"{letter}:\\"
        return ctypes.windll.kernel32.GetDriveTypeW(root)


    def get_volume_info(letter):
        root = f"{letter}:\\"
        buf = ctypes.create_unicode_buffer(1024)
        fsbuf = ctypes.create_unicode_buffer(1024)
        serial = ctypes.c_ulong()
        maxfile = ctypes.c_ulong()
        flags = ctypes.c_ulong()
        try:
            res = ctypes.windll.kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(root),
                buf,
                ctypes.sizeof(buf),
                ctypes.byref(serial),
                ctypes.byref(maxfile),
                ctypes.byref(flags),
                fsbuf,
                ctypes.sizeof(fsbuf),
            )
            if res:
                return buf.value, fsbuf.value
        except Exception:
            pass
        return "", ""


    def run_proc(cmd):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, shell=True)
            return p.returncode, p.stdout + p.stderr
        except Exception as e:
            return 1, str(e)


    def run_powershell_file(script_text):
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.ps1') as f:
            f.write(script_text)
            path = f.name
        cmd = f'powershell -NoProfile -ExecutionPolicy Bypass -File "{path}"'
        rc, out = run_proc(cmd)
        try:
            os.remove(path)
        except Exception:
            pass
        return rc, out


    def list_removable_drives():
        MAX_BYTES = 256 * 1024 * 1024 * 1024  # 256 GiB
        drives = []
        ps_script = r"""
    $usb = Get-WmiObject Win32_DiskDrive -Filter "InterfaceType='USB'"
    foreach ($d in $usb) {
      $index = $d.Index
      $size = $d.Size
      $model = ($d.Model -replace '\|',' ')
      $parts = Get-WmiObject Win32_DiskDriveToDiskPartition | Where-Object { $_.Antecedent -like "*" + $d.DeviceID + "*" }
      if ($parts) {
        foreach ($p in $parts) {
          $partid = ($p.Dependent -replace '.*DeviceID="(.*)".*','$1')
          $lds = Get-WmiObject Win32_LogicalDiskToPartition | Where-Object { $_.Antecedent -like "*" + $partid + "*" }
          if ($lds) {
            foreach ($ld in $lds) {
              $drive = ($ld.Dependent -replace '.*DeviceID="(.*)".*','$1')
              $vol = Get-WmiObject Win32_LogicalDisk -Filter "DeviceID='$drive'"
              $fs = ''
              $vn = ''
              if ($vol) { $fs = $vol.FileSystem; $vn = $vol.VolumeName }
              $health = ''
              try { $health = (Get-Volume -DriveLetter $drive.TrimEnd(':') -ErrorAction SilentlyContinue).HealthStatus } catch { $health = '' }
              Write-Output "$($drive)|$($vn)|$($fs)|$size|$index|$model|$health"
            }
          } else {
            Write-Output "| | |$size|$index|$model|"
          }
        }
      } else {
        Write-Output "| | |$size|$index|$model|"
      }
    }
    """
        rc, out = run_powershell_file(ps_script)
        if rc == 0 and out.strip():
            for line in out.splitlines():
                parts = line.split('|')
                if len(parts) < 7:
                    continue
                drive, label, fs, size_s, diskindex, model, health = parts
                try:
                    size = int(size_s) if size_s and size_s.isdigit() else 0
                except Exception:
                    size = 0
                if size and size > MAX_BYTES:
                    continue
                if drive and drive.strip():
                    letter = drive.strip()[0]
                    drives.append((letter, label or "", fs or "", size, diskindex, model or "", health or ""))
                else:
                    drives.append((f"DISK{diskindex}", label or "", fs or "", size, diskindex, model or "", health or ""))

        # fallback: scan mounted letters
        if not drives:
            for i in range(65, 91):
                letter = chr(i)
                try:
                    t = get_drive_type(letter)
                except Exception:
                    continue
                if t in (DRIVE_REMOVABLE, DRIVE_FIXED):
                    path = f"{letter}:\\"
                    if os.path.exists(path):
                        label, fs = get_volume_info(letter)
                        size = 0
                        try:
                            rc2, out2 = run_proc(f"powershell -NoProfile -Command \"(Get-Volume -DriveLetter {letter}).Size\"")
                            if rc2 == 0 and out2.strip().isdigit():
                                size = int(out2.strip())
                        except Exception:
                            size = 0
                        if size and size > MAX_BYTES:
                            continue
                        drives.append((letter, label or "", fs or "", size, '', '', ''))
        return drives


    # UI globals
    root = None
    listbox = None
    output = None


    def log(text):
        if output:
            output.insert('end', text + '\n')
            output.see('end')
        else:
            print(text)


    def refresh():
        listbox.delete(0, 'end')
        drives = list_removable_drives()
        for d in drives:
            if isinstance(d[0], str) and d[0].startswith('DISK'):
                display = f"{d[0]} - {int(d[3]/1024/1024/1024) if d[3] else 0}GB - {d[5]} - {d[6]}"
            else:
                display = f"{d[0]}:\\ - {d[1]} - {d[2]} - {int(d[3]/1024/1024/1024) if d[3] else 0}GB - {d[5]} - {d[6]}"
            listbox.insert('end', display)
        log('Refreshed drive list')


    def get_selected():
        sel = listbox.curselection()
        if not sel:
            messagebox.showinfo('Select drive', 'Please select a drive from the list')
            return None
        item = listbox.get(sel[0])
        token = item.split()[0]
        if token.endswith(':\\'):
            letter = token[0]
            if letter.upper() == 'C':
                messagebox.showerror('Refuse', 'Refusing to operate on system drive C:')
                return None
            return letter.upper()
        return token


    def confirm_drive(letter, action):
        prompt = f"Type the drive letter {letter} to confirm {action}:"
        val = simpledialog.askstring('Confirm', prompt)
        return val and val.strip().upper() == letter


    def clear_readonly():
        sel = get_selected()
        if not sel:
            return
        if isinstance(sel, str) and sel.startswith('DISK'):
            messagebox.showinfo('Disk selected', 'Disk-level operations are not yet supported for this action. Please select a drive letter.')
            return
        letter = sel
        if not is_admin():
            messagebox.showwarning('Administrator required', 'Run as Administrator and try again.')
            return
        if not confirm_drive(letter, 'clearing read-only flags'):
            return
        ps = f'Get-Partition -DriveLetter {letter} | Get-Disk | Set-Disk -IsReadOnly $false -ErrorAction SilentlyContinue'
        rc, out = run_powershell(ps)
        log(out or f'Clear readonly returned code {rc}')


    def fix_permissions():
        sel = get_selected()
        if not sel:
            return
        if isinstance(sel, str) and sel.startswith('DISK'):
            messagebox.showinfo('Disk selected', 'Permissions operations require a mounted drive letter.')
            return
        letter = sel
        if not is_admin():
            messagebox.showwarning('Administrator required', 'Run as Administrator and try again.')
            return
        if not confirm_drive(letter, 'taking ownership and granting full control'):
            return
        path = f"{letter}:\\"
        cmd1 = f'takeown /f "{path}" /r /d y'
        rc1, out1 = run_proc(cmd1)
        log(out1)
        cmd2 = f'icacls "{path}" /grant *S-1-5-32-544:F /t /c'
        cmd3 = f'icacls "{path}" /grant %USERNAME%:F /t /c'
        rc2, out2 = run_proc(cmd2)
        rc3, out3 = run_proc(cmd3)
        log(out2)
        log(out3)


    def attrib_reset():
        sel = get_selected()
        if not sel:
            return
        if isinstance(sel, str) and sel.startswith('DISK'):
            messagebox.showinfo('Disk selected', 'Attrib reset requires a mounted drive letter.')
            return
        letter = sel
        if not confirm_drive(letter, 'removing read-only/hidden/system attributes from files'):
            return
        cmd = f'attrib -r -s -h /s /d "{letter}:\\*.*"'
        rc, out = run_proc(cmd)
        log(out or f'attrib returned {rc}')


    def format_drive(fs, quick=True):
        sel = get_selected()
        if not sel:
            return
        if isinstance(sel, str) and sel.startswith('DISK'):
            messagebox.showinfo('Disk selected', 'Disk-level formatting not implemented in UI. Use DiskPart manually.')
            return
        letter = sel
        if not is_admin():
            messagebox.showwarning('Administrator required', 'Run as Administrator and try again.')
            return
        if not confirm_drive(letter, f'format to {fs}'):
            return
        ps = f'Format-Volume -DriveLetter {letter} -FileSystem {fs} -Force -Confirm:$false'
        rc, out = run_powershell(ps)
        if rc == 0:
            log(out or f'Formatted {letter}: to {fs} (PowerShell)')
            return
        if quick:
            cmd = f'echo Y| format {letter}: /FS:{fs} /Q'
        else:
            cmd = f'echo Y| format {letter}: /FS:{fs}'
        rc2, out2 = run_proc(cmd)
        log(out2 or f'format returned {rc2}')


    def wipe_and_format():
        sel = get_selected()
        if not sel:
            return
        if isinstance(sel, str) and sel.startswith('DISK'):
            messagebox.showinfo('Disk selected', 'Disk-level wipe not implemented in UI.')
            return
        letter = sel
        if not is_admin():
            messagebox.showwarning('Administrator required', 'Run as Administrator and try again.')
            return
        if not confirm_drive(letter, 'wiping (zeroing first 100MB) and formatting'):
            return
        try:
            largefile = f"{letter}:\\__wipe_tmp.bin"
            cmd = f'fsutil file createnew "{largefile}" 104857600'
            rc, out = run_proc(cmd)
            log(out)
            try:
                os.remove(largefile)
            except Exception as e:
                log(str(e))
        except Exception as e:
            log('Wipe helper failed: ' + str(e))
        format_drive('NTFS', quick=True)


    def build_ui():
        global root, listbox, output
        root = tk.Tk()
        root.title('USB Permission/Formatter (procedural)')

        info = tk.StringVar()
        info.set('Run as Administrator for full functionality')
        tk.Label(root, textvariable=info).pack(fill='x')

        listbox = tk.Listbox(root, width=80, height=10)
        listbox.pack(padx=8, pady=6)

        btn_frame = tk.Frame(root)
        btn_frame.pack(fill='x', padx=8)

        tk.Button(btn_frame, text='Refresh', command=refresh).pack(side='left')
        tk.Button(btn_frame, text='Clear ReadOnly', command=clear_readonly).pack(side='left')
        tk.Button(btn_frame, text='Fix Permissions', command=fix_permissions).pack(side='left')
        tk.Button(btn_frame, text='Attrib Reset', command=attrib_reset).pack(side='left')
        tk.Button(btn_frame, text='Quick Format NTFS', command=lambda: format_drive('NTFS', quick=True)).pack(side='left')
        tk.Button(btn_frame, text='Quick Format FAT32', command=lambda: format_drive('FAT32', quick=True)).pack(side='left')

        tk.Button(root, text='Wipe (zero first 100MB) + Format', command=wipe_and_format).pack(pady=6)

        output = tk.Text(root, height=12)
        output.pack(fill='both', padx=8, pady=6, expand=True)

        refresh()
        return root


    def main():
        app = build_ui()
        app.mainloop()


    if __name__ == '__main__':
        main()
