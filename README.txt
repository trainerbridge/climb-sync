Climb Sync
===============

What it does:
  Syncs your Wahoo Kickr Climb's tilt to the current Zwift road grade,
  even during ERG-mode workouts where Zwift normally disables grade
  simulation.

Requires:
  - Wahoo Kickr v6 trainer + Kickr Climb on the same WiFi network as your PC
  - Sauce4Zwift running locally (https://www.sauce.llc/products/sauce4zwift/)
  - Zwift connected to the Kickr (BLE; Zwift's default - DO NOT switch
    Zwift to "Direct Connect" for the Kickr; this app uses that channel)

How to run:
  1. Double-click climb-sync.exe.
  2. The app icon appears in the system tray (right of the clock).
       Red:    not connected.
       Yellow: partially connected (one of KICKR or Sauce4Zwift).
       Green:  live - Climb tracking grade.
  3. Right-click the tray icon for status, override IP, restart sync,
     or exit.
  4. Logs at: %APPDATA%\climb-sync\logs\app.log

If Windows SmartScreen warns "Windows protected your PC":
  - This is normal for unsigned indie tools. Click "More info" then
    "Run anyway" (one-time per .exe download).

Override KICKR IP:
  If mDNS auto-discovery doesn't find your Kickr:
  Right-click the tray icon -> "Override KICKR IP..." -> enter the IP
  (e.g. 192.168.1.50). The app saves it to:
    %APPDATA%\climb-sync\config.toml
  and restarts the sync automatically.

To uninstall:
  Delete climb-sync.exe.
  Optional: delete %APPDATA%\climb-sync\ (contains config + logs).

Issues / source:
  https://github.com/coffeefueled/climb-sync
