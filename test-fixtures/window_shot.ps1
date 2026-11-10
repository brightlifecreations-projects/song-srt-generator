# Screenshot of one window's own content (PrintWindow), never the screen: whatever overlaps it
# isn't captured. Test fixture.
#   powershell -File test-fixtures\window_shot.ps1 <pid> <out.png> [-Title "<window title>"]
param([int]$ProcessId, [string]$Out, [string]$Title = "")
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class W {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern IntPtr FindWindow(string c, string t);
}
"@
[W]::SetProcessDPIAware() | Out-Null
$h = if ($Title) { [W]::FindWindow([NullString]::Value, $Title) } else { (Get-Process -Id $ProcessId).MainWindowHandle }
if ($h -eq [IntPtr]::Zero) { Write-Error "no window"; exit 1 }
$r = New-Object W+RECT
[W]::GetWindowRect($h, [ref]$r) | Out-Null
$bmp = New-Object System.Drawing.Bitmap ($r.R - $r.L), ($r.B - $r.T)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
[W]::PrintWindow($h, $hdc, 2) | Out-Null  # PW_RENDERFULLCONTENT: includes WebView2 content
$g.ReleaseHdc($hdc)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
"saved $Out ($($bmp.Width)x$($bmp.Height))"
