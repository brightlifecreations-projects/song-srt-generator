# Click at (x, y) inside a window (window coordinates, physical pixels), but only if that window
# is the foreground window right now: a test must never click into another app. Test fixture.
#   powershell -File test-fixtures\click_in_window.ps1 "<window title>" <x> <y>
param([string]$Title, [int]$X, [int]$Y)
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class C {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, int x, int y, uint d, UIntPtr e);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern IntPtr FindWindow(string c, string t);
}
"@
[C]::SetProcessDPIAware() | Out-Null
$h = [C]::FindWindow([NullString]::Value, $Title)
if ($h -eq [IntPtr]::Zero) { Write-Error "no window titled '$Title'"; exit 1 }
if ([C]::GetForegroundWindow() -ne $h) { Write-Error "the app window isn't in front; not clicking"; exit 2 }
$r = New-Object C+RECT
[C]::GetWindowRect($h, [ref]$r) | Out-Null
[C]::SetCursorPos($r.L + $X, $r.T + $Y) | Out-Null
Start-Sleep -Milliseconds 100
if ([C]::GetForegroundWindow() -ne $h) { Write-Error "the app window lost the front; not clicking"; exit 2 }
[C]::mouse_event(0x2, 0, 0, 0, [UIntPtr]::Zero); [C]::mouse_event(0x4, 0, 0, 0, [UIntPtr]::Zero)
"clicked at window ($X, $Y)"
