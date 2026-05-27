# cardputer-zero-default-apps

`cardputer-zero-default-apps` contains the default small-screen Linux
applications for Cardputer Zero Shell.

These programs are ordinary GUI applications. They run inside the already
authenticated Linux user session, create normal compositor-managed windows, and
turn existing Linux capabilities into 320x170 handheld interfaces.

The project goal is intentionally narrow: make system tasks usable on the
Cardputer Zero screen without inventing a new system layer.

## Scope

This repository owns the default application UI and thin backend adapters for:

- Files
- Settings
- System Monitor
- Power
- Terminal
- Robot

It does not own:

- login
- PAM
- user creation
- session startup
- seat management
- polkit agents
- window management
- global shortcut policy
- system permission policy

Those responsibilities belong to `cardputer-zero-os`, `cardputer-zero-shell`,
or the standard Linux stack.

## Architecture

```text
cardputer-zero-os
  -> login, session, labwc, polkit, system services

cardputer-zero-shell
  -> launcher, task switcher, APPLaunch scanning

cardputer-zero-default-apps
  -> normal GTK applications
  -> small-screen UI
  -> Linux command, D-Bus, procfs, sysfs, and file adapters
```

The default apps call existing tools such as:

```text
systemd / loginctl / NetworkManager / PipeWire / procfs / sysfs / gio
```

When an operation needs authorization, the app calls the normal system command
and lets Linux, systemd, logind, or polkit handle the policy. This repository
does not add `sudo` wrappers, password storage, or a separate permission model.

## Visual Language

The apps use the same small-screen design language as Cardputer Zero OS and
Shell:

- retro paper UI
- clean monochrome handheld UI
- 1px black outlines
- warm paper backgrounds
- hard 1px or 2px shadows
- orange selected state
- keyboard-first interaction
- fixed 320x170 windows

Theme tokens:

| Token | Hex | Role |
| --- | --- | --- |
| Zero Paper | `#E9E4D5` | screen background |
| Panel Cream | `#F4F0E6` | panels and controls |
| Icon Well | `#F8F4EA` | keycaps, icon wells, light fills |
| Ink Black | `#171717` | text and icons |
| Line Black | `#2A2A2A` | outlines and separators |
| Muted Text | `#6E6A61` | secondary values |
| Accent Orange | `#E66A2C` | focus and active state |
| OK Green | `#3A7D44` | healthy state |
| Warn Red | `#B94A2C` | warnings and dangerous actions |
| Hard Shadow | `#BDB5A4` | hard shadow |

## Files

`zero-files` is the small-screen file manager. It provides a compact file list,
keyboard/action hints, path/status information, menu actions, and an inline
properties view.

It uses the normal Linux filesystem as its source of truth. File operations are
thin adapters over Python file APIs and system tools such as `gio open` and
`gio trash`.

### File List

![Files file list](docs/assets/zero-files-functional-320x170.png)

### Menu

![Files menu](docs/assets/zero-files-menu-320x170.png)

### Properties

![Files properties](docs/assets/zero-files-properties-320x170.png)

## Settings

`zero-settings` is the small-screen settings application. It is a normal Linux
GUI program, not a system policy service.

![Settings](docs/assets/screenshots/settings.png)

The UI is split into a category area and a detail area. Current categories are:

- System
- Display
- Network
- Sound
- Power
- About

Settings reads system facts through existing Linux commands and files. Examples
include `hostnamectl`, `localectl`, `timedatectl`, `nmcli`, `wpctl`, `pactl`,
`brightnessctl`, `/etc/os-release`, and `/sys`.

User preferences owned by the default apps are stored under:

```text
~/.config/cardputer-zero/default-apps/
```

Session preferences that need to be consumed by Cardputer Zero OS are stored
under:

```text
~/.config/cardputer-zero/session/
```

The screen timeout setting is written to
`~/.config/cardputer-zero/session/display-power.json`; the OS session wires that
preference to the standard Wayland tools that actually blank and wake the
internal screen.

The app does not implement login, session, polkit, or permission rules. If a
setting requires authorization, the backend calls the standard command and the
system handles the result.

## System Monitor

`zero-system-monitor` shows current system information in small, single-topic
tabs:

- CPU
- RAM
- Disk
- Network
- Temperature

The monitor reads live Linux data from `/proc`, `/sys`, `ps`, `df`, `ip`, and
thermal interfaces. The CPU and RAM pages emphasize the top four processes so
the view stays useful on a 320x170 screen. Network shows Wi-Fi and Ethernet
addresses when those interfaces exist.

### CPU

![System Monitor CPU](docs/assets/screenshots/monitor-cpu.png)

### RAM

![System Monitor RAM](docs/assets/screenshots/monitor-ram.png)

### Disk

![System Monitor Disk](docs/assets/screenshots/monitor-disk.png)

### Network

![System Monitor Network](docs/assets/screenshots/monitor-network.png)

### Temperature

![System Monitor Temperature](docs/assets/screenshots/monitor-temperature.png)

## Power

`zero-power-menu` is a standalone quick action panel for power operations. It is
not the Settings power page and does not manage power policy.

![Power](docs/assets/screenshots/power.png)

Actions:

- Display Off
- Suspend
- Reboot
- Shutdown
- Logout

Dangerous actions use an in-app confirmation page. The backend calls normal
Linux commands:

```text
systemctl suspend
systemctl reboot
systemctl poweroff
loginctl terminate-session
```

Display-off support uses available compositor/system tools when present, such
as `wlopm` or `xset`. Logout targets the active Cardputer Zero session rather
than killing arbitrary shell processes.

## Terminal

`zero-terminal` is the default terminal entry for Cardputer Zero. It is a
small-screen terminal front end with tab management and terminal rendering. The
target behavior is a frameless 320x170 terminal app, not a large desktop
terminal window.

![Terminal](docs/assets/screenshots/terminal.png)

## Robot

`zero-robot` is a small-screen Pi agent frontend. It connects keyboard text
input and optional speech-to-text transcripts to `pi --mode rpc`, then renders
compact agent status, output, and errors in a 320x170 Cardputer Zero
interface. Thinking and tool streams are intentionally collapsed into short
status labels so the final response remains readable on the small display.

![Robot](docs/assets/screenshots/robot-current.png)

Robot does not implement a new agent runtime or a new permission model. Its
tools selector maps directly to Pi tool allowlists:

```text
SAFE -> --tools read,grep,find,ls
EDIT -> --tools read,grep,find,ls,edit,write
FULL -> --tools read,grep,find,ls,edit,write,bash
```

Robot intentionally does not show per-tool approval prompts. The selected tools
profile is the permission boundary exposed by the app; execution semantics
belong to Pi, the current Linux user, and standard Linux authorization systems.
Speech-to-text is treated only as an input adapter: the transcript is shown
before it is used as a Pi prompt.

The app includes a compact settings page for the pieces that would otherwise be
hard to configure on a handheld screen:

- Pi tools profile
- working directory
- Pi command
- provider
- model
- session directory
- offline mode
- recorder backend
- recording duration
- speech-to-text model

## Desktop Entries

The installer writes APPLaunch entries to:

```text
/usr/share/APPLaunch/applications/
```

Current entries:

```text
10-zero-settings.desktop
20-zero-terminal.desktop
30-zero-files.desktop
40-zero-system-monitor.desktop
50-zero-robot.desktop
90-zero-power-menu.desktop
100-zero-app-store.desktop
```

Each desktop entry declares Cardputer Zero metadata such as:

```ini
X-Zero-AppId=...
X-Zero-Display=wayland
```

Launcher icons are installed as PNG assets under
`/usr/share/APPLaunch/share/images/`.

## Install

Required packages:

```sh
sudo apt-get install python3 python3-gi gir1.2-gtk-4.0
```

Recommended packages:

```sh
sudo apt-get install foot brightnessctl network-manager pipewire-pulse \
  libglib2.0-bin trash-cli packagekit alsa-utils ffmpeg
```

Robot also expects Pi for execution and the Python OpenAI package plus
`OPENAI_API_KEY` for speech-to-text. When those optional pieces are missing,
`zero-robot` shows an unavailable/error state rather than crashing.
During a real `sudo ./install.sh` install, the script prepares the Robot runtime
by installing `nodejs`, `npm`, and `@earendil-works/pi-coding-agent` when `pi`
is missing.
Set `SKIP_ROBOT_RUNTIME=1` to skip this network/package-manager step, which is
what Debian packaging does.

Install from the source tree:

```sh
sudo ./install.sh
```

Installed paths:

```text
/usr/bin/zero-settings
/usr/bin/zero-terminal
/usr/bin/zero-files
/usr/bin/zero-power-menu
/usr/bin/zero-system-monitor
/usr/bin/zero-app-store
/usr/bin/zero-robot
/usr/lib/python3/dist-packages/czero_apps/
/usr/share/APPLaunch/applications/*.desktop
/usr/share/APPLaunch/icons/*.svg
```

## Development

Run from the source tree:

```sh
PYTHONPATH=src python3 -m czero_apps.main settings
PYTHONPATH=src python3 -m czero_apps.main monitor
PYTHONPATH=src python3 -m czero_apps.main files
PYTHONPATH=src python3 -m czero_apps.main terminal
PYTHONPATH=src python3 -m czero_apps.main power
PYTHONPATH=src python3 -m czero_apps.main robot
```

Syntax check:

```sh
python3 -m compileall src
```

Build a device deployment package locally with Docker:

```sh
sh scripts/docker-build-arm64.sh
```

The Docker build does not install Robot runtime dependencies and does not build
on the target device. It validates Python syntax and writes:

```text
.docker-out/cardputer-zero-default-apps.tar.gz
```

Deploy that archive to the device, unpack it, and install with:

```sh
SKIP_ROBOT_RUNTIME=1 sh install.sh
```

For the current multi-repository device fix bundle, build all three local
Docker outputs first:

```sh
sh scripts/build-device-fixes.sh
```

Then stage the built artifacts to a device:

```sh
cd ../cardputer-zero-default-apps
sh scripts/stage-device-fixes.sh pi@192.168.50.35
```

This only uploads artifacts and writes `/tmp/czero-deploy-fixes/install-as-root.sh`.
The device still needs root privileges to install into `/usr` and `/opt`.
