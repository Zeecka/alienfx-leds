#!/usr/bin/env bash
# Install AlienFX LEDs system-wide (daemon, CLI, app) and the GNOME extension for
# the invoking user. Every installed path is recorded in a manifest so that
# uninstall.sh removes exactly what was installed, nothing else.
set -euo pipefail
cd "$(dirname "$0")"
[[ $EUID -eq 0 ]] || exec sudo --preserve-env=SUDO_USER "$0" "$@"
USER_NAME="${SUDO_USER:-$(logname)}"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"
MAN=/usr/local/share/alienfix/MANIFEST
EXT_UUID=alienfix@zeecka.github.io
EXT_DIR="$USER_HOME/.local/share/gnome-shell/extensions/$EXT_UUID"

put() {  # put MODE SRC DST
    install -D -m "$1" "$2" "$3"; echo "$3" >>"$MAN.new"
}
mkdir -p /usr/local/share/alienfix; : >"$MAN.new"
# migration from the first version (French UI "AlienFix Éclairage", fixed layout.json)
rm -f /usr/local/bin/alienfix-gui /usr/local/share/alienfix/layout.json

for f in src/alienfix/*.py; do put 0644 "$f" "/usr/local/lib/alienfix/alienfix/$(basename "$f")"; done
for f in data/chassis/*.json;  do put 0644 "$f" "/usr/local/share/alienfix/chassis/$(basename "$f")"; done
for f in data/profiles/*.json; do put 0644 "$f" "/usr/local/share/alienfix/profiles/$(basename "$f")"; done
for f in data/models/*.json;   do put 0644 "$f" "/usr/local/share/alienfix/models/$(basename "$f")"; done
put 0644 data/io.github.zeecka.AlienFix1.xml   /usr/local/share/alienfix/io.github.zeecka.AlienFix1.xml
put 0644 README.md                             /usr/local/share/doc/alienfix/README.md
put 0755 bin/alienfixd                         /usr/local/bin/alienfixd
put 0755 bin/alienfix                          /usr/local/bin/alienfix
put 0755 gui/alienfx-leds                      /usr/local/bin/alienfx-leds
put 0644 data/system/alienfixd.service         /etc/systemd/system/alienfixd.service
put 0644 data/system/io.github.zeecka.AlienFix.conf    /usr/share/dbus-1/system.d/io.github.zeecka.AlienFix.conf
put 0644 data/system/io.github.zeecka.AlienFix.service /usr/share/dbus-1/system-services/io.github.zeecka.AlienFix.service
put 0644 data/system/io.github.zeecka.alienfix.policy  /usr/share/polkit-1/actions/io.github.zeecka.alienfix.policy
put 0644 data/io.github.zeecka.AlienFix.desktop        /usr/local/share/applications/io.github.zeecka.AlienFix.desktop
put 0644 data/icons/io.github.zeecka.AlienFix.svg      /usr/local/share/icons/hicolor/scalable/apps/io.github.zeecka.AlienFix.svg

# GNOME Shell extension, per user (owned by the user, like any user extension)
for f in "gnome-extension/$EXT_UUID"/*; do
    install -D -m 0644 -o "$USER_NAME" -g "$USER_NAME" "$f" "$EXT_DIR/$(basename "$f")"
    echo "$EXT_DIR/$(basename "$f")" >>"$MAN.new"
done
echo "$EXT_DIR/" >>"$MAN.new"
mv "$MAN.new" "$MAN"

python3 -m compileall -q /usr/local/lib/alienfix
systemctl daemon-reload
systemctl reload dbus
gtk-update-icon-cache -q -t /usr/local/share/icons/hicolor 2>/dev/null || true
update-desktop-database -q /usr/local/share/applications 2>/dev/null || true
systemctl enable alienfixd.service
systemctl restart alienfixd.service      # an upgrade must not keep the old code running
echo "installed; manifest: $MAN ($(wc -l <"$MAN") entries)"
