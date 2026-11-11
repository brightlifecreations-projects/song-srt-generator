# SRT Generator 4.8 (Mac): report

**Released:** https://github.com/brightlifecreations-projects/song-srt-generator/releases/tag/v4.8
4.8 is installed in /Applications and running (server reports 4.8; its post-update warmup passed).

## What passed

| Check | Result |
|---|---|
| Unit tests (`tests/`) | **123 pass** (3 skipped: Windows-only, as before) |
| UI suite `test-fixtures/ui_v48.js` in the Mac's WebKit window | **104/104** (every download compared with the Review panel, cue for cue, to the millisecond) |
| `warmup.py` | **PASS** (isolation 2.7 s, alignment 2.4 s, listening check 3.2 s) |
| Downloaded SRT = Review panel | Confirmed by the harness after every action, including edits, typed symbols, flags cleared, AI undo, Line/Word switches |
| Feeds | `…/releases/latest/download/appcast.xml` serves **4.8**; `…/appcast-windows.xml` serves **4.7**, byte-identical to v4.7's |
| Installed app | Replaced in /Applications (not through Sparkle), signature valid, opened, up in 18 s, version 4.8, its own warmup PASS |

PRs: #19 (V4.8) and #20 (rebuilt frontend bundle; the bundle embeds the version number and I'd built it before bumping VERSION, same as #16 for 4.6.1). Both merged with merge commits, branches deleted, tag v4.8. PR #18 was already merged when I started.

## Real runs (listening check on, final AI check with your saved Claude key, claude-haiku-5-5)

| File | Captions | OK | Fixed | To check | Listening check took | Whole job |
|---|---|---|---|---|---|---|
| Sample clip (17 s, lyrics) | 5 | 5 | 0 | 0 | **3.3 s** | 12.6 s |
| Kabhi Kabhi Aditi (3:38, Hinglish lyrics) | 96 | 77 | 0 | 19 | **111 s** | 174 s |
| Hinglish reel "Instagram Reels Video 74396" (2:18, transcribe, Accurate) | 119 | 115 | 2 | 2 | **58 s** | 549 s (429 s of it transcription) |

**What it fixed and flagged:**
- **Reel, fixed:** two captions re-timed about 0.4 s earlier to where the words are actually heard ("Standard ka paper dena", "application based questions").
- **Reel, flagged:** two timing flags: "1 ghante ka 25 marks" and "but agle saal yaani" each appear 0.6–0.7 s after the word starts.
- **Reel, Claude:** "do" → "2" twice (kept; the check counts "do" and "2" as the same number), [?] on "Celebas" and "has". 16 of its line-cut changes were refused by the existing 4.6.1 rule checks.
- **Aditi, flagged:** 13 of the 19 flags are in the repeated outro (2:52–3:28). There Whisper hears "thoda" where the captions say "husde husde…" or "Nahi toh bas", or hears nothing. My reading is that the aligner slipped a line on the repeated chorus, but **I couldn't listen, so please check those by ear**. The other 6: four timing flags (a caption 0.6–1.6 s off its word), "husde tu zara" heard as "hansate", and "Ye Aditi jaane" (timing).
- **Aditi, Claude:** 15 changes, all "aditi" → "Aditi".

**How much time the check adds:** about 3 s on a short clip, about 1 minute on a 2-minute reel (+12% of that job, which is mostly transcription), and about 2 minutes on a 3½-minute song (that job's time roughly triples). Songs cost the most: Whisper hears sung words less clearly, so more captions get the second, one-caption listen.

**Problems the real runs found, all fixed before release:**
- The first Aditi run took 352 s. Whisper costs almost as much for a 4 s clip as for 30 s, so I now pack the one-caption re-listens into shared calls. That brought it to 111 s.
- 22 false flags on Aditi, mostly Hinglish spellings of the same word ("fir"/"phir", "woh"/"vah"). These now match.
- The check "put back" "do" where Claude had correctly written "2", and flagged "heard ‘tin’, caption says ‘3’". A number word and its digits now always count as equal.
- A swapped-letter AI typo ("yeasr") sounded close enough to "years" to pass. An AI change is now put back when the audio says exactly the original.
- Two more caught by the UI suite: one Word-by-word false flag ("for" timed by the other "for" nearby), and English "older"/"younger" accidentally passing under the looser Hinglish threshold.

## Skipped because it needs a person
- **Listening** to the flagged Aditi outro, to confirm it really is mistimed.
- **Sparkle's update window:** 4.8 was installed by replacing the app, as you asked. Existing Macs on 4.6.1 will be offered 4.8 by Sparkle; I didn't click through that.
- **The macOS Save panel:** the test harness answers it automatically.
- **The Windows 4.8 installer:** it needs your Windows PC. The prompt is below.
- Nothing needed a Keychain dialog. The Sparkle signing key and your saved Claude key were both read without a prompt. The Claude key was read through the installed app's own Python for exactly that reason.

## Decisions I made on my own (also in DECISIONS.md V4.8)
1. **"Original pass" means the text before the final AI check.** The listening check only ever changes words the AI changed: it puts the original back when the audio says exactly the original. Your lyrics and approved transcripts are never changed by it, only flagged.
2. **The listening check doesn't clear the AI's [?].** Hearing a word can't confirm its spelling ("Aditi"/"Aditee" sound the same). You clear a [?] by clicking it or editing the word, and **Verified** needs no flags and no [?] left.
3. **Speed:** captions are re-heard in shared windows of up to 28 s, and only doubtful ones get a second listen on their own clip, packed together. One Whisper call per caption would have cost minutes.
4. **The Hinglish model is never downloaded just for the check.** Without it, Whisper medium listens in Hindi and the result is converted to Roman letters.
5. **Word by word keeps 4.6's bridge** (a word stays up until the next one when the gap is under 1 s). The "end within 0.3 s" rule allows that bridge.
6. **Hyphens become spaces** ("ex-wife" → "ex wife"). In Word by word that stays one caption, because it's one spoken word with one time span.
7. **The Hindi full stop (।) is kept.** "..." counts as an ellipsis and is removed.
8. **"one" now becomes 1** unless the exceptions list or "the/this/which one" keeps it. So "not giving up one inch" → "1 inch" (in 4.6 it stayed a word).
   - New default exceptions: everyone, as one, become one, we are one, at first, first of all, first things first, first sight, a second.
   - "second" is an ordinal only after the/my/his… (so "wait a second" stays).
   - Thousands have no comma (10000), because captions carry no commas.
9. **Hinglish counting** converts runs of 3 or more ("ek do teen" → 1 2 3), or 2 with no ambiguous word. "mujhe ek do" ("give me one") stays words. Added "tin" as a spelling of teen.
10. **The AI check refuses changes one at a time** (one that adds or drops a word, empties a word, or swaps in an unrelated word), with reasons in the job log. Before, one bad change threw away the whole answer.
11. **The UI suite's speech files were regenerated on this Mac** with the built-in voices (the Windows ones weren't here). The mock AI now recognises the final check by the request's shape, not the old prompt wording.
12. **A guard test now fails** if a new Python module is missing from the release scripts' file lists (`listen_check.py` would otherwise have crashed the app on import).

## Cleanup
- All test data folders, test settings, generated media, logs and the test OpenAI key are removed. The Keychain was checked afterwards: no test key left, your Anthropic key untouched.
- The old 4.6.1 app copy and old build folders and zips are deleted; `dist-release/` holds only the 4.8 zip and its appcast.
- The installed app's post-update warmup created one job folder in your App Support `jobs/`. That's the app's normal update step, not a test file of mine.

## Prompt for Claude Code on the Windows laptop

Paste this in the project folder (e.g. `C:\srt`):

```
Add the Windows 4.8 installer to the existing v4.8 GitHub release of SRT Generator. Work on your own; don't ask me anything unless something would need a person.

1. git checkout main, git pull. Check that VERSION says 4.8 and the latest commit includes "Rebuilt frontend bundle for 4.8".
2. Update the private environment if needed: .venv\Scripts\python -m pip install -r requirements-windows.txt (nothing new is expected; 4.8 adds no packages).
3. Run the unit tests: .venv\Scripts\python -m unittest discover -s tests. All must pass.
4. Run warmup in a separate test data folder (set SRT_DATA_DIR to a test folder, and link or copy its models folder from the real one so nothing downloads again). It must print PASS with a "listening check" timing line.
5. Run the Windows UI suite with test-fixtures\ui_edge.py and test-fixtures\ui_v48.js (not ui_v46.js), with test-fixtures\mock_ai.py as the mock provider, in a separate data folder. Media, in order: sample\clip.wav, a short drama video with pauses, a reel video with a Hindi file name and background music, and a Hinglish MP3. Make them with Windows voices and ffmpeg like V4.6 did. The Reels script in ui_v48.js now has a 6th line ("Maya: The first show starts at seven thirty."), so the reel audio must speak it. Fix anything that fails and is a real bug, retest, and note Windows-only test differences in DECISIONS.md.
6. Build and publish: .\make_release_windows.ps1 4.8 -Publish. Release v4.8 already exists with the Mac zip, appcast.xml, and a copy of v4.7's appcast-windows.xml; the script replaces that with the new signed one. The EdDSA key comes from this PC's Credential Manager; never print or commit it.
7. Verify both feeds:
   - https://github.com/brightlifecreations-projects/song-srt-generator/releases/latest/download/appcast-windows.xml must serve 4.8 and point to SRT-Generator-4.8-Setup.exe.
   - .../releases/latest/download/appcast.xml must still serve 4.8 (the Mac build), unchanged.
8. Install 4.8 over the installed 4.7 by running the new Setup.exe (not through WinSparkle), open the app, and confirm its Updates page says 4.8. Then delete old build copies in dist-release.
9. Add a short "Windows" note to the V4.8 entry in DECISIONS.md (results, timings, anything fixed), commit it on a branch, open a PR, merge it with a merge commit, delete the branch and pull main. Remove every test file, test setting and test key you created. Then give me a short report.
```
