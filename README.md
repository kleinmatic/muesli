# muesli

A small tray app that records both sides of a Zoom or Google Meet call on
Linux, the way Granola does on a Mac. A play on Granola. It only records:
transcription and notes happen elsewhere (MacWhisper on a Mac, then Granola).

## Use

1. Start **Muesli** from the app menu (or run `muesli`). It sits in the system tray.
2. Click the tray icon, or press **Record**, before the call. The icon turns red
   and the window shows a timer and two live meters: **Me** and **Them**.
3. Click the red dot, or press **Stop**, when the call ends. Muesli merges the two sides into one file
   and notifies you when it's ready.

Clicking the tray icon toggles: the grey dot starts a recording, the red dot
stops it.

Recordings go to `~/Meetings/<date_time>[-name]/`:

| File | What |
|---|---|
| `<folder>.m4a` | both sides mixed, mono AAC, about 30 MB/hour. Give this to MacWhisper |
| `me.flac` | your microphone |
| `them.flac` | everything your speakers or headphones played |
| `session.json` | start/end times and the devices used |

Tell the people on the call that you're recording.

## How it works

- **Me** is the default microphone. **Them** is the monitor of the default
  output. Both are looked up by name when you press Record. Muesli doesn't
  create or change anything in PipeWire or WirePlumber.
- Each side is `parecord --raw` -> muesli -> `ffmpeg` -> FLAC. The meters are
  computed from the same bytes that are written to disk, so a moving meter
  means that audio is being saved.
- FLAC is written continuously. If muesli or the laptop dies mid-call, both
  tracks are intact up to that moment, and the next launch offers to finish
  the merge.
- The merge evens out the loudness of each side (`loudnorm`), mixes them, and
  limits the result. It runs at low priority: about 3 minutes for a one-hour
  call on a 2015 MacBook Air.

## Warnings

Muesli shows a desktop notification, and brings its window forward, when:

- a recorder stops, or a side sends no audio data at all for 3 seconds;
- the other side has been silent for 5 minutes while your mic is active
  (usually the call's sound is going to a device that isn't the default output);
- disk space drops below 300 MB.

## Limits

- Devices are chosen when recording starts. If you switch headsets mid-call,
  stop and start a new recording. Change devices through the system default,
  not inside Zoom or Meet.
- The two tracks can start up to about 0.15 s apart. That doesn't matter for
  transcription.
- No call detection yet: you have to press Record.

## Install

Debian 13: `sudo apt install python3-pyside6.qtwidgets python3-pyside6.qtnetwork ffmpeg pulseaudio-utils`,
then `./install.sh` (add `--autostart` to start at login as just the tray dot,
`--uninstall` to remove).

## Other ways to run it

- `muesli --hidden`: start in the tray without opening the window.

- `muesli --headless [--seconds N] [name]`: record from a terminal, with a level
  line per second. Ctrl+C stops and merges.
- `MUESLI_DIR=/some/folder`: record somewhere other than `~/Meetings`.
- `MUESLI_SELFTEST=1 QT_QPA_PLATFORM=offscreen MUESLI_DIR=$(mktemp -d) muesli`:
  drives the real window through record, stop and merge, and exits 0 on success.

## License

Copyright (C) 2026 Scott Klein.

muesli is free software: you can redistribute it and/or modify it under the
terms of version 2 of the GNU General Public License as published by the Free
Software Foundation. It comes with no warranty. See [LICENSE](LICENSE).

muesli is not affiliated with Granola.

## Credits

Written by Scott Klein with Claude (Anthropic's AI model), working in Claude
Code. Claude is credited as co-author on the commits.
