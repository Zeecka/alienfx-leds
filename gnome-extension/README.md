# GNOME Shell extension "AlienFX LEDs"

Adds a **Lighting** toggle to GNOME Quick Settings (GNOME 46): on/off,
brightness, effect presets, and a shortcut to the AlienFX LEDs app. It talks
to the `alienfixd` system service over D-Bus, asynchronously only.

Install (done by `install.sh`): copy `alienfix@zeecka.github.io/` to
`~/.local/share/gnome-shell/extensions/`, reload GNOME Shell on X11
(Alt+F2, `r`), then `gnome-extensions enable alienfix@zeecka.github.io`.

Pitfall: if `gsettings get org.gnome.shell disable-user-extensions` is
`true`, user extensions are silently ignored.
