#!/usr/bin/env bash
# Remove exactly what install.sh recorded in its manifest, plus the state dir.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo --preserve-env=SUDO_USER "$0" "$@"
MAN=/usr/local/share/alienfix/MANIFEST
[[ -f $MAN ]] || { echo "no manifest at $MAN: nothing installed by install.sh"; exit 0; }
USER_NAME="${SUDO_USER:-$(logname)}"
sudo -u "$USER_NAME" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u "$USER_NAME")/bus" \
    gnome-extensions disable alienfix@zeecka.github.io 2>/dev/null || true
systemctl disable --now alienfixd.service 2>/dev/null || true
while read -r p; do
    case "$p" in
        */) rmdir "$p" 2>/dev/null || true ;;
        /usr/local/*|/etc/systemd/system/alienfixd.service|/usr/share/dbus-1/*AlienFix*|/usr/share/polkit-1/actions/io.github.zeecka.alienfix.policy|*/gnome-shell/extensions/alienfix@zeecka.github.io/*)
            rm -f -- "$p" ;;
        *) echo "refusing to remove unexpected path: $p" ;;
    esac
done <"$MAN"
rm -rf /usr/local/lib/alienfix /usr/local/share/alienfix /usr/local/share/doc/alienfix /var/lib/alienfix
systemctl daemon-reload; systemctl reload dbus
echo "uninstalled"
