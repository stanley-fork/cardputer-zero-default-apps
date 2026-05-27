# Specification

`cardputer-zero-default-apps` is a collection of ordinary Linux GUI
applications optimized for the Cardputer Zero internal screen.

## In Scope

- GTK4/Wayland small-screen UI.
- Settings, Terminal, Files, Power, System Monitor, Robot, and App Store entry
  points.
- Shared list/detail/dialog/status components.
- Command execution wrapper with timeout, stdout, stderr, and exit code.
- APPLaunch desktop entries and icons.
- Thin backends over existing Linux commands, procfs, sysfs, and user files.
- Writing user-session preferences consumed by the Cardputer Zero OS session,
  such as `~/.config/cardputer-zero/session/display-power.json`.

## Out Of Scope

- Login or greeter.
- PAM.
- User creation.
- Session launch.
- DRM/KMS setup.
- labwc configuration.
- Seat assignment.
- Global Tab/Esc policy.
- Polkit agent.
- Privileged helper design.
- Package-manager implementation.
- Agent runtime implementation.
- Independent Pi agent or system permission model.
- Global microphone wake word or always-on recorder.

## Permission Rule

The apps do not decide authorization. They invoke normal Linux commands. If a
command needs privilege, the system's polkit policy and active polkit agent
decide whether to allow it.

`zero-robot` does not define a separate agent permission model. It presents
Pi agent tools profile, working-directory, prompt, output, tool activity, and
errors in a 320x170 interface. Execution and authorization semantics remain
owned by Pi, the current Linux user, and standard Linux authorization systems.
Any Robot UI mode must map directly to Pi tool allowlists.

Speech-to-text is an input adapter only. Voice transcripts must be visible to
the user before they are sent to Pi.

Robot must expose its Pi and speech configuration in-app. Users must not be
required to edit JSON manually for normal setup such as Pi command path,
provider, model, tools profile, working directory, recorder backend, recording
duration, STT model, or offline mode.

## Display Rule

Every app must be a Wayland-capable GUI application and must declare:

```ini
X-Zero-Display=wayland
```

No app in this repository may own `/dev/fb*`, DRM devices, input devices, or
global keyboard shortcuts.

The Settings app may write the user's screen-timeout preference, but the OS
session owns wiring that preference to compositor idle and output power tools.

## Failure Rule

Missing optional commands are shown as unavailable. They must not crash the app.
