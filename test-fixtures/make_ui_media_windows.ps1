# (Saved as UTF-8 with a BOM: Windows PowerShell 5.1 reads a script without one as ANSI,
# which garbles the Hindi file name below.)
# Test media for test-fixtures/ui_v48.js and ui_v49.js on Windows: the counterpart of
# make_ui_media_mac.sh, with Windows' own (OneCore) voices and the bundled ffmpeg. Test fixture.
#   powershell -ExecutionPolicy Bypass -File test-fixtures\make_ui_media_windows.ps1   -> test-fixtures\.work\ui\ (git-ignored)
# Voices: Zira and David (en-US) for Samantha and Daniel, Ravi (en-IN) for Rishi's romanised Hindi.
# Music: $env:MUSIC if set, otherwise a 60-second synthesized bed (chords and a beat) made with ffmpeg.
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $Here
$FF = Join-Path $Root "bin\ffmpeg.exe"
if (-not (Test-Path $FF)) { $FF = (Get-Command ffmpeg).Source }
$Out = Join-Path $Here ".work\ui"
New-Item -ItemType Directory -Force $Out | Out-Null
Set-Location $Out
function q { & $FF -v error -y @args; if ($LASTEXITCODE -ne 0) { throw "ffmpeg failed: $args" } }

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
function Say([string]$Voice, [string]$Text, [string]$File) {
    $s = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
    $s.Voice = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices | Where-Object { $_.DisplayName -like "*$Voice*" } | Select-Object -First 1
    if (-not $s.Voice) { throw "voice $Voice is not installed" }
    $stream = Await ($s.SynthesizeTextToStreamAsync($Text)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
    $reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
    $n = [uint32]$stream.Size
    Await ($reader.LoadAsync($n)) ([uint32]) | Out-Null
    $bytes = New-Object byte[] $n
    $reader.ReadBytes($bytes)
    [IO.File]::WriteAllBytes((Join-Path $Out $File), $bytes)
}
# Phrases with a pause after each (the Mac script's [[slnc N]]), joined into one WAV.
function SayWithPauses([string]$Voice, [string[]]$Parts, [double]$Pause, [string]$File) {
    $inputs = @(); $i = 0
    foreach ($p in $Parts) {
        Say $Voice $p "part$i.wav"
        q -i "part$i.wav" -af "apad=pad_dur=$Pause" -ar 44100 -ac 1 "padded$i.wav"
        $inputs += @("-i", "padded$i.wav"); $i++
    }
    q @inputs -filter_complex "concat=n=$($Parts.Count):v=0:a=1" $File
    Remove-Item part*.wav, padded*.wav
}

$Music = if ($env:MUSIC) { $env:MUSIC } else {
    q -f lavfi -i ("aevalsrc='0.10*sin(2*PI*if(lt(mod(t,8),2),220,if(lt(mod(t,8),4),174.61,if(lt(mod(t,8),6),196,164.81)))*t)" +
        "+0.07*sin(2*PI*if(lt(mod(t,8),2),277.18,if(lt(mod(t,8),4),220,if(lt(mod(t,8),6),246.94,207.65)))*t)" +
        "+0.35*sin(2*PI*55*t)*exp(-18*mod(t,0.5))':s=44100:d=60") -ac 2 music.wav
    "music.wav"
}

SayWithPauses "Zira" @("Where were you last night?", "I was at the hospital for two hours.", "Why did nobody tell me that?", "You never asked me anything.") 1.0 "drama.wav"
q -f lavfi -i color=c=navy:s=640x360:r=25 -i drama.wav -shortest "-c:v" libx264 -pix_fmt yuv420p "-c:a" aac drama.mp4

$Lines = @(@("Zira", "For twelve years I have been waking up at seven a.m. to train."),
           @("David", "In week one we lost seventy percent of our budget on ads that nobody even watched."),
           @("David", "She walked into the store for her son and asked for the shoes in a size four."),
           @("Zira", "I am not giving up one inch."),
           @("David", "The first show starts at seven thirty."))
$Labels = @("Coach", "Maya", "Maya", "Coach", "Maya")
for ($i = 0; $i -lt $Lines.Count; $i++) {
    Say $Lines[$i][0] $Lines[$i][1] "r$i-raw.wav"
    q -i "r$i-raw.wav" -af apad=pad_dur=0.8 -ar 44100 -ac 1 "r$i.wav"
}
q -i r0.wav -i r1.wav -i r2.wav -i r3.wav -i r4.wav -filter_complex "concat=n=5:v=0:a=1" speech.wav
q -i speech.wav -i $Music -filter_complex "[1:a]volume=0.25[m];[0:a][m]amix=inputs=2:duration=first:normalize=0" -ar 44100 reel.wav
q -f lavfi -i color=c=darkred:s=360x640:r=25 -i reel.wav -shortest "-c:v" libx264 -pix_fmt yuv420p "-c:a" aac "गाना रील टेस्ट.mp4"

# Long: the five lines six times, over the music looped.
Set-Content -Encoding ascii list.txt ((1..6 | ForEach-Object { "file 'speech.wav'" }) -join "`n")
q -f concat -safe 0 -i list.txt -c copy long-speech.wav
q -i long-speech.wav -stream_loop -1 -i $Music -filter_complex "[1:a]volume=0.2[m];[0:a][m]amix=inputs=2:duration=first:normalize=0" -ar 44100 long-song.wav
q -f lavfi -i color=c=darkgreen:s=360x640:r=25 -i long-song.wav -shortest "-c:v" libx264 -pix_fmt yuv420p "-c:a" aac long-reel.mp4
q -f lavfi -i color=c=indigo:s=640x360:r=25 -i long-song.wav -shortest "-c:v" libx264 -pix_fmt yuv420p "-c:a" aac long-drama.mp4
$script = foreach ($n in 1..6) { for ($i = 0; $i -lt $Lines.Count; $i++) { "$($Labels[$i]): $($Lines[$i][1])" } }
[IO.File]::WriteAllText((Join-Path $Out "long-script.txt"), (($script -join "`n") + "`n"))

SayWithPauses "Ravi" @("Kal ke board exam ke liye bahut padhai karni hai.", "Mujhe do ghante aur chahiye.", "Ek do teen, chalo shuru karte hain.") 0.6 "hing.wav"
q -i hing.wav "-b:a" 128k hinglish.mp3

Remove-Item -ErrorAction SilentlyContinue r?-raw.wav, r?.wav, speech.wav, reel.wav, long-speech.wav, list.txt, drama.wav, hing.wav
Get-ChildItem | Format-Table Name, Length -AutoSize
