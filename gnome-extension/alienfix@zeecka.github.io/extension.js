// AlienFX LEDs — Quick Settings toggle for the alienfixd system daemon.
//
// Everything goes through D-Bus (system bus, io.github.zeecka.AlienFix). Golden
// rule: no synchronous call. The proxy is created asynchronously and every
// method is called through its ...Async() variant, never blocking the shell:
// state comes back through the StateChanged signal.

import GObject from 'gi://GObject';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import St from 'gi://St';
import Shell from 'gi://Shell';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';
import {Slider} from 'resource:///org/gnome/shell/ui/slider.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'io.github.zeecka.AlienFix';
const OBJECT_PATH = '/io/github/zeecka/AlienFix';
const DESKTOP_ID = 'io.github.zeecka.AlienFix.desktop';
const ERROR_NOT_AUTHORIZED = 'io.github.zeecka.AlienFix1.Error.NotAuthorized';
const BRIGHTNESS_DEBOUNCE_MS = 150;

// Subset of data/io.github.zeecka.AlienFix1.xml: only what the extension uses.
const IFACE_XML = `
<node>
  <interface name="io.github.zeecka.AlienFix1">
    <method name="GetState">
      <arg name="state_json" type="s" direction="out"/>
    </method>
    <method name="SetKeyboardEffect">
      <arg name="name" type="s" direction="in"/>
      <arg name="tempo" type="y" direction="in"/>
      <arg name="c1" type="(yyy)" direction="in"/>
      <arg name="c2" type="(yyy)" direction="in"/>
    </method>
    <method name="SetBrightness">
      <arg name="percent" type="y" direction="in"/>
    </method>
    <method name="SetEnabled">
      <arg name="enabled" type="b" direction="in"/>
    </method>
    <method name="SetRipple">
      <arg name="enabled" type="b" direction="in"/>
      <arg name="color" type="(yyy)" direction="in"/>
      <arg name="speed" type="y" direction="in"/>
      <arg name="under" type="s" direction="in"/>
      <arg name="background" type="(yyy)" direction="in"/>
    </method>
    <signal name="StateChanged">
      <arg name="state_json" type="s"/>
    </signal>
  </interface>
</node>`;

const AlienFixProxy = Gio.DBusProxy.makeProxyWrapper(IFACE_XML);

const EFFECT_LABELS = {
    static: 'Static',
    breathing: 'Breathing',
    wave: 'Wave',
    pulse: 'Pulse',
    mixpulse: 'Two-color pulse',
    nightrider: 'Sweep',
};

const PRESETS = [
    {label: 'Static (current color)', effect: 'static', tempo: 7},
    {label: 'Wave', effect: 'wave', tempo: 5},
    {label: 'Breathing', effect: 'breathing', tempo: 7},
];

function toColor(value) {
    if (!Array.isArray(value) || value.length !== 3)
        return null;
    if (!value.every(v => Number.isFinite(v)))
        return null;
    return value.map(v => Math.max(0, Math.min(255, Math.round(v))));
}

function modeLabel(state) {
    if (!state.enabled)
        return 'Off';
    const kb = state.keyboard ?? {};
    if (kb.mode === 'effect') {
        const name = kb.effect?.name;
        return EFFECT_LABELS[name] ?? name ?? 'Effect';
    }
    return 'Static';
}

const AlienFixIndicator = GObject.registerClass(
class AlienFixIndicator extends QuickSettings.SystemIndicator {
    _init() {
        super._init();

        this._proxy = null;
        this._proxyGeneration = 0;
        this._proxySignalId = 0;
        this._cancellable = null;
        this._watchId = 0;
        this._brightnessTimeoutId = 0;
        this._signals = [];

        this._available = false;
        this._state = null;
        this._errorSubtitle = null;
        this._syncingSlider = false;

        this._buildUi();
        this._syncUi();

        // Watch the daemon: called at once (appeared or vanished), then on
        // every start / stop of the service.
        this._watchId = Gio.bus_watch_name(
            Gio.BusType.SYSTEM,
            BUS_NAME,
            Gio.BusNameWatcherFlags.NONE,
            () => this._onNameAppeared(),
            () => this._onNameVanished());
    }

    _connect(obj, signal, handler) {
        this._signals.push([obj, obj.connect(signal, handler)]);
    }

    _buildUi() {
        this._toggle = new QuickSettings.QuickMenuToggle({
            title: 'Lighting',
            subtitle: 'Connecting…',
            iconName: 'keyboard-brightness-symbolic',
            toggleMode: true,
        });
        // In toggleMode, 'checked' is already flipped when 'clicked' arrives.
        this._connect(this._toggle, 'clicked',
            () => this._call('SetEnabled', this._toggle.checked));

        const menu = this._toggle.menu;
        menu.setHeader('keyboard-brightness-symbolic', 'AlienFX LEDs');

        // Brightness 0..100 (the Slider works in 0..1).
        this._sliderItem = new PopupMenu.PopupBaseMenuItem({activate: false});
        this._sliderItem.add_child(new St.Icon({
            icon_name: 'display-brightness-symbolic',
            style_class: 'popup-menu-icon',
        }));
        this._slider = new Slider(0);
        this._slider.x_expand = true;
        this._slider.accessible_name = 'Brightness';
        this._sliderItem.add_child(this._slider);
        this._connect(this._slider, 'notify::value',
            () => this._onSliderChanged());
        menu.addMenuItem(this._sliderItem);

        // Typing ripple: the daemon draws it and saves it (per-key keyboards only).
        this._rippleItem = new PopupMenu.PopupSwitchMenuItem('Typing ripple', false);
        this._connect(this._rippleItem, 'toggled',
            (_item, on) => this._setRipple(on));
        menu.addMenuItem(this._rippleItem);

        this._presetItems = PRESETS.map(preset => {
            const item = new PopupMenu.PopupMenuItem(preset.label);
            this._connect(item, 'activate', () => this._applyPreset(preset));
            menu.addMenuItem(item);
            return item;
        });

        menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        const openItem = new PopupMenu.PopupMenuItem('Open AlienFX LEDs…');
        this._connect(openItem, 'activate', () => this._openApp());
        menu.addMenuItem(openItem);

        this.quickSettingsItems.push(this._toggle);
    }

    // --- D-Bus -----------------------------------------------------------

    _onNameAppeared() {
        if (this._proxy || this._cancellable)
            return;

        const generation = ++this._proxyGeneration;
        const cancellable = new Gio.Cancellable();
        this._cancellable = cancellable;

        // Async form of the wrapper: init_async, callback (proxy, error).
        AlienFixProxy(Gio.DBus.system, BUS_NAME, OBJECT_PATH,
            (proxy, error) => {
                if (generation !== this._proxyGeneration)
                    return; // stale proxy (disabled, daemon gone)
                this._cancellable = null;

                if (error) {
                    if (!error.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                        console.warn(`AlienFX LEDs: D-Bus proxy unavailable: ${error.message}`);
                    this._available = false;
                    this._syncUi();
                    return;
                }

                this._proxy = proxy;
                this._proxySignalId = proxy.connectSignal('StateChanged',
                    (_proxy, _sender, [json]) => this._applyStateJson(json));
                this._available = true;
                this._syncUi();
                this._refreshState();
            },
            cancellable,
            Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES |
            Gio.DBusProxyFlags.DO_NOT_AUTO_START);
    }

    _onNameVanished() {
        this._dropProxy();
        this._available = false;
        this._state = null;
        this._syncUi();
    }

    _dropProxy() {
        this._proxyGeneration++;
        if (this._cancellable) {
            this._cancellable.cancel();
            this._cancellable = null;
        }
        if (this._proxy && this._proxySignalId)
            this._proxy.disconnectSignal(this._proxySignalId);
        this._proxySignalId = 0;
        this._proxy = null;
        this._clearBrightnessTimeout();
    }

    async _refreshState() {
        const proxy = this._proxy;
        if (!proxy)
            return;
        try {
            const [json] = await proxy.GetStateAsync();
            if (proxy === this._proxy)
                this._applyStateJson(json);
        } catch (e) {
            if (proxy === this._proxy)
                this._handleError('GetState', e);
        }
    }

    // Fire and forget: the result comes back through StateChanged.
    _call(method, ...args) {
        const proxy = this._proxy;
        if (!proxy) {
            this._syncUi();
            return;
        }
        proxy[`${method}Async`](...args).catch(e => {
            if (proxy === this._proxy)
                this._handleError(method, e);
        });
    }

    _handleError(method, error) {
        let remote = null;
        if (error instanceof GLib.Error && Gio.DBusError.is_remote_error(error))
            remote = Gio.DBusError.get_remote_error(error);

        if (remote === ERROR_NOT_AUTHORIZED) {
            console.warn(`AlienFX LEDs: ${method} refused (not authorized)`);
            this._errorSubtitle = 'Not allowed';
        } else {
            console.warn(`AlienFX LEDs: ${method} failed: ${error.message}`);
            this._errorSubtitle = 'Error';
        }
        // Put the switch and the slider back on the last known state.
        this._syncUi();
    }

    _applyStateJson(json) {
        let state;
        try {
            state = JSON.parse(json);
        } catch (e) {
            console.warn(`AlienFX LEDs: unreadable state JSON: ${e.message}`);
            return;
        }
        this._state = state;
        this._errorSubtitle = null;
        this._available = true;
        this._syncUi();
    }

    // --- Interface ---------------------------------------------------------

    _syncUi() {
        const state = this._state;
        let subtitle;

        if (!this._available) {
            subtitle = 'Service unavailable';
            this._toggle.checked = false;
        } else if (state) {
            subtitle = this._errorSubtitle ?? modeLabel(state);
            this._toggle.checked = !!state.enabled;
            this._syncSlider(state.brightness);
        } else {
            subtitle = this._errorSubtitle ?? 'Connecting…';
        }

        const sensitive = this._available;
        this._toggle.reactive = sensitive;
        this._sliderItem.setSensitive(sensitive && !!state);
        this._presetItems.forEach(item => item.setSensitive(sensitive && !!state));
        const ripple = state?.ripple;
        this._rippleItem.visible = !!ripple?.available;
        this._rippleItem.setSensitive(sensitive && !!ripple && !!state.enabled);
        this._rippleItem.setToggleState(!!ripple?.enabled);

        this._toggle.subtitle = subtitle;
        this._toggle.menu.setHeader('keyboard-brightness-symbolic', 'AlienFX LEDs', subtitle);
    }

    _syncSlider(brightness) {
        if (!Number.isFinite(brightness))
            return;
        // Do not move the slider under the user's finger.
        if (this._slider._dragging || this._brightnessTimeoutId)
            return;
        this._syncingSlider = true;
        this._slider.value = Math.max(0, Math.min(100, brightness)) / 100;
        this._syncingSlider = false;
    }

    _onSliderChanged() {
        if (this._syncingSlider)
            return; // change came from the state: no SetBrightness
        this._clearBrightnessTimeout();
        this._brightnessTimeoutId = GLib.timeout_add(GLib.PRIORITY_DEFAULT,
            BRIGHTNESS_DEBOUNCE_MS, () => {
                this._brightnessTimeoutId = 0;
                this._call('SetBrightness', Math.round(this._slider.value * 100));
                return GLib.SOURCE_REMOVE;
            });
    }

    _clearBrightnessTimeout() {
        if (this._brightnessTimeoutId) {
            GLib.source_remove(this._brightnessTimeoutId);
            this._brightnessTimeoutId = 0;
        }
    }

    _currentColors() {
        const kb = this._state?.keyboard ?? {};
        const effect = kb.effect ?? {};
        const keys = kb.keys ?? {};
        const firstKey = toColor(keys[Object.keys(keys)[0]]);
        const effectC1 = toColor(effect.c1);

        // In static mode the "current color" is the keys' color (zone
        // keyboards have no per-key colors: use the base color).
        const base = toColor(kb.base);
        const c1 = (kb.mode === 'effect' ? effectC1 ?? firstKey : firstKey ?? base ?? effectC1) ??
            [255, 255, 255];
        const c2 = toColor(effect.c2) ?? c1;
        return [c1, c2];
    }

    _setRipple(on) {
        const r = this._state?.ripple;
        if (!r)
            return;
        this._call('SetRipple', on, toColor(r.color) ?? [255, 110, 0], r.speed ?? 10,
            r.under === 'color' ? 'color' : 'effect', toColor(r.background) ?? [0, 0, 25]);
    }

    _applyPreset(preset) {
        const [c1, c2] = this._currentColors();
        this._call('SetKeyboardEffect', preset.effect, preset.tempo, c1, c2);
    }

    _openApp() {
        Main.panel.closeQuickSettings();

        const app = Shell.AppSystem.get_default().lookup_app(DESKTOP_ID);
        if (app) {
            app.activate();
            return;
        }

        const info = Gio.DesktopAppInfo.new(DESKTOP_ID);
        if (!info) {
            console.warn(`AlienFX LEDs: ${DESKTOP_ID} not found`);
            return;
        }
        try {
            info.launch([], global.create_app_launch_context(0, -1));
        } catch (e) {
            console.warn(`AlienFX LEDs: cannot launch the app: ${e.message}`);
        }
    }

    destroy() {
        if (this._watchId) {
            Gio.bus_unwatch_name(this._watchId);
            this._watchId = 0;
        }
        this._dropProxy();
        this._clearBrightnessTimeout();

        for (const [obj, id] of this._signals)
            obj.disconnect(id);
        this._signals = [];

        this.quickSettingsItems.forEach(item => item.destroy());
        this.quickSettingsItems = [];
        this._toggle = null;
        this._slider = null;
        this._sliderItem = null;
        this._presetItems = [];
        this._rippleItem = null;

        super.destroy();
    }
});

export default class AlienFixExtension extends Extension {
    enable() {
        this._indicator = new AlienFixIndicator();
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}
