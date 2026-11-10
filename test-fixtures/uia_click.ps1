# Click a control in a window by name, through Windows UI Automation. Test fixture.
#   powershell -File test-fixtures\uia_click.ps1 "<window title>" "<control name>" [-List]
# Menu items are expanded or invoked; buttons invoked. -List prints the window's named controls.
param([string]$Window, [string]$Name, [switch]$List, [int]$TimeoutSec = 20, [string]$Type = "")
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::RootElement
$deadline = (Get-Date).AddSeconds($TimeoutSec)
$win = $null
while (-not $win -and (Get-Date) -lt $deadline) {
    $cond = New-Object System.Windows.Automation.PropertyCondition ([System.Windows.Automation.AutomationElement]::NameProperty), $Window
    $win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
    if (-not $win) { Start-Sleep -Milliseconds 300 }
}
if (-not $win) { Write-Error "window '$Window' not found"; exit 1 }
if ($List) {
    $all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($e in $all) { if ($e.Current.Name) { "{0} | {1}" -f $e.Current.ControlType.ProgrammaticName, $e.Current.Name } }
    exit 0
}
$el = $null
while (-not $el -and (Get-Date) -lt $deadline) {
    $cond = New-Object System.Windows.Automation.PropertyCondition ([System.Windows.Automation.AutomationElement]::NameProperty), $Name
    if ($Type) {  # e.g. MenuItem, Button
        $typeCond = New-Object System.Windows.Automation.PropertyCondition ([System.Windows.Automation.AutomationElement]::ControlTypeProperty), ([System.Windows.Automation.ControlType]::$Type)
        $cond = New-Object System.Windows.Automation.AndCondition $cond, $typeCond
    }
    $el = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
    if (-not $el) { $el = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond) }
    if (-not $el) { Start-Sleep -Milliseconds 300 }
}
if (-not $el) { Write-Error "control '$Name' not found in '$Window'"; exit 1 }
$pattern = $null
if ($el.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$pattern)) {
    $pattern.Expand(); "expanded '$Name'"
} elseif ($el.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) {
    $pattern.Invoke(); "invoked '$Name'"
} else { Write-Error "'$Name' can't be clicked"; exit 1 }
