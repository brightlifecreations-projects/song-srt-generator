#!/bin/bash
# Test media for test-fixtures/ui_v48.js on a Mac (macOS voices + the bundled ffmpeg). Test fixture.
#   bash test-fixtures/make_ui_media_mac.sh      -> test-fixtures/.work/ui/ (git-ignored)
# Short: drama.mp4 (pauses between turns), "गाना रील टेस्ट.mp4" (two voices + background music,
# vertical), hinglish.mp3 (Rishi reading romanised Hindi). Long (V4.8.1 follow-playback check):
# long-reel.mp4 (vertical), long-drama.mp4 (horizontal), long-song.wav — the reel script spoken six
# times over the music, about 2 minutes and ~90 captions; long-script.txt is its script.
set -euo pipefail
cd "$(dirname "$0")"
FF="$(command -v ffmpeg || echo "$PWD/../bin/ffmpeg")"
MUSIC="${MUSIC:-.work/song-60s.mp3}"
OUT=.work/ui; mkdir -p "$OUT"; cd "$OUT"
q() { "$FF" -v error -y "$@"; }
say -v Samantha -o drama.aiff "Where were you last night? [[slnc 1000]] I was at the hospital for two hours. [[slnc 1000]] Why did nobody tell me that? [[slnc 1000]] You never asked me anything."
q -f lavfi -i color=c=navy:s=640x360:r=25 -i drama.aiff -shortest -c:v libx264 -pix_fmt yuv420p -c:a aac drama.mp4
LINES=("Samantha|For twelve years I have been waking up at seven a.m. to train."
       "Daniel|In week one we lost seventy percent of our budget on ads that nobody even watched."
       "Daniel|She walked into the store for her son and asked for the shoes in a size four."
       "Samantha|I am not giving up one inch."
       "Daniel|The first show starts at seven thirty.")
LABELS=(Coach Maya Maya Coach Maya)
for i in "${!LINES[@]}"; do
  say -v "${LINES[$i]%%|*}" -o "r$i.aiff" "${LINES[$i]#*|}"
  q -i "r$i.aiff" -af apad=pad_dur=0.8 -ar 44100 -ac 1 "r$i.wav"
done
q -i r0.wav -i r1.wav -i r2.wav -i r3.wav -i r4.wav -filter_complex "concat=n=5:v=0:a=1" speech.wav
q -i speech.wav -i "../../$MUSIC" -filter_complex "[1:a]volume=0.25[m];[0:a][m]amix=inputs=2:duration=first:normalize=0" -ar 44100 reel.wav
q -f lavfi -i color=c=darkred:s=360x640:r=25 -i reel.wav -shortest -c:v libx264 -pix_fmt yuv420p -c:a aac "गाना रील टेस्ट.mp4"
# Long: the five lines six times, over the music looped.
printf "file 'speech.wav'\n%.0s" 1 2 3 4 5 6 > list.txt
q -f concat -safe 0 -i list.txt -c copy long-speech.wav
q -i long-speech.wav -stream_loop -1 -i "../../$MUSIC" -filter_complex "[1:a]volume=0.2[m];[0:a][m]amix=inputs=2:duration=first:normalize=0" -ar 44100 long-song.wav
q -f lavfi -i color=c=darkgreen:s=360x640:r=25 -i long-song.wav -shortest -c:v libx264 -pix_fmt yuv420p -c:a aac long-reel.mp4
q -f lavfi -i color=c=indigo:s=640x360:r=25 -i long-song.wav -shortest -c:v libx264 -pix_fmt yuv420p -c:a aac long-drama.mp4
: > long-script.txt
for n in 1 2 3 4 5 6; do for i in "${!LINES[@]}"; do echo "${LABELS[$i]}: ${LINES[$i]#*|}" >> long-script.txt; done; done
say -v Rishi -o hing.aiff "Kal ke board exam ke liye bahut padhai karni hai. [[slnc 600]] Mujhe do ghante aur chahiye. [[slnc 600]] Ek do teen, chalo shuru karte hain."
q -i hing.aiff -b:a 128k hinglish.mp3
rm -f ./*.aiff r?.wav speech.wav reel.wav long-speech.wav list.txt
ls -la
