# Googly Eyes for Waybar

A whimsical Waybar module that puts two googly eyes in your Waybar that follows the mouse cursor. Mouse clicks make the matching eye blink, and the config file allows you to change basic settings for both eyes together or separately.

## Features

- Cursor-following pupils
- Full-circle eye tracking for Waybar at the top or bottom of the screen
- Global left/right click blink effects via Hyprland binds
- Configurable colors, sizes, blink character, and enabled eyes
- Hot-reloaded TOML config
- Single Python script, no Python package dependencies

## Install

Copy the script and example config:

```sh
mkdir -p ~/.config/waybar/scripts
cp scripts/waybar-googly-eyes.py ~/.config/waybar/scripts/
chmod +x ~/.config/waybar/scripts/waybar-googly-eyes.py
cp scripts/waybar-googly-eyes-launcher.sh ~/.config/waybar/scripts/
chmod +x ~/.config/waybar/scripts/waybar-googly-eyes-launcher.sh
cp examples/googly-eyes.toml ~/.config/waybar/googly-eyes.toml
```

Add the image module to your Waybar config:

```jsonc
"modules-center": [
  "image#googly-eyes"
],

"image#googly-eyes": {
  "path": "/home/YOU/.cache/waybar/googly-eyes.svg",
  "size": 44,
  "interval": 0.016,
  "tooltip": true
}
```

Start the launcher from Hyprland autostart:

```ini
exec-once = uwsm-app -- /home/YOU/.config/waybar/scripts/waybar-googly-eyes-launcher.sh
```

Optional global blink binds for Hyprland:

```ini
bindn = , mouse:272, exec, /home/YOU/.config/waybar/scripts/waybar-googly-eyes.py --button left down
bindrn = , mouse:272, exec, /home/YOU/.config/waybar/scripts/waybar-googly-eyes.py --button left up
bindn = , mouse:273, exec, /home/YOU/.config/waybar/scripts/waybar-googly-eyes.py --button right down
bindrn = , mouse:273, exec, /home/YOU/.config/waybar/scripts/waybar-googly-eyes.py --button right up
```

Reload Hyprland and restart Waybar.

The launcher keeps polling for the Waybar config module and restarts the daemon if it exits, which avoids a boot-time race where the script could miss startup and never come back.

The script detects Waybar position from `~/.config/waybar/config.jsonc`. If you manage Waybar position through a separate state file, it also supports `~/.config/waybar/display-state.json` with a `position` value of `top` or `bottom`. You can force the position with `WAYBAR_GOOGLE_EYES_POSITION=top` or `WAYBAR_GOOGLE_EYES_POSITION=bottom`.

## Settings

Edit `~/.config/waybar/googly-eyes.toml`.

```toml
[eyes]
sclera_color = "#fffdf2"
pupil_color = "#111111"
outline_color = "#1d2021"
size = 1.0
distance = 38.0
pupil_size = 7.3
blink_size = 22.0

[blink]
enabled = true
duration_ms = 140
character = "—"

[left]
enabled = true

[right]
enabled = true
```

`[left]` and `[right]` may override `enabled`, `sclera_color`, `pupil_color`, `outline_color`, `size`, `pupil_size`, and `blink_size`.

## To Do

- Improve distance handling so increasing eye distance can resize the Waybar module width without shrinking the eyes.
- Add a cleaner installer.

## License

Released under the [MIT License](https://github.com/aloglu/googly-eyes/blob/main/LICENSE).
