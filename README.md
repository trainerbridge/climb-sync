# climb-sync

Sync the Wahoo Kickr Climb's tilt to Zwift road grade — even during ERG-mode
structured workouts, where Zwift normally disables grade simulation.

The Kickr keeps holding your workout power target (Zwift's job, over BLE/FTMS)
while climb-sync tells the trainer the current road grade over Wahoo's
Direct Connect (DIRCON) TCP transport. The two transports coexist on the
trainer, so the Climb moves with the terrain while ERG keeps the watts steady.

## Requirements

**Hardware**
- Wahoo Kickr v6 trainer (firmware that supports DIRCON / "Direct Connect")
- Wahoo Kickr Climb paired to the Kickr
- Windows PC on the same Wi-Fi network as the trainer

**Software**
- [Sauce4Zwift](https://www.sauce.llc/products/sauce4zwift/) running locally
  (climb-sync reads the current road grade from its WebSocket API)
- Zwift connected to the Kickr over **BLE** (Zwift's default). Do NOT switch
  Zwift to "Direct Connect" for the Kickr — climb-sync needs that channel.

**To build from source**
- Python 3.12+

## Run the prebuilt exe

1. Grab `climb-sync.exe` from the GitHub Releases page.
2. Double-click it. A tray icon appears next to the clock.
   - Red: not connected
   - Yellow: partially connected (one of Kickr or Sauce4Zwift)
   - Green: live — Climb is tracking grade
3. Right-click the tray icon for status, IP override, restart, or exit.
4. Logs: `%APPDATA%\climb-sync\logs\app.log`

If mDNS auto-discovery doesn't find your Kickr, right-click the tray icon →
"Override KICKR IP..." and enter the trainer's LAN IP. It's saved to
`%APPDATA%\climb-sync\config.toml`.

## Run from source

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m climb_sync
```

## Build the Windows exe

```bat
pip install -r requirements.txt
pip install pyinstaller==6.19.0
pyinstaller climb_sync.spec
```

The single-file exe lands at `dist\climb-sync.exe`. See
[`docs/RELEASING.md`](docs/RELEASING.md) for the full release checklist.

## Project layout

```
src/climb_sync/      application package
  dircon/              Wahoo Direct Connect TCP client + frame codec
  grade/               Sauce4Zwift WebSocket grade source
  sync/                grade -> climb sync loop, smoothing, staleness
  tray/                pystray UI, dialogs, status icons
  lifecycle/           logging, single-instance lock
  app.py               composition root
  __main__.py          CLI entry, --smoke flag
assets/app.ico         Windows file icon
scripts/build_exe.py   thin wrapper around pyinstaller
climb_sync.spec      pyinstaller build config
```

## A note on how this was built

This project was vibecoded — designed and implemented in collaboration with
Claude, using the [GSD](https://github.com/coffeefueled) planning workflow.
The architecture is the result of an exploratory feasibility phase that
ruled out the obvious BLE path before landing on the DIRCON transport that
actually works on current Kickr v6 firmware. If a piece of the code looks
over-commented or over-explained, that's the AI pair-programming origin
showing through. PRs welcome.

## License

MIT — see [LICENSE](LICENSE).
