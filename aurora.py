#!/usr/bin/env python3
"""
Aurora Lite - a lightweight per-monitor wallpaper manager (image + video).

One DESKTOP-type window per monitor, kept below everything. Right-click a
screen for a menu: 
    1. play/pause
    2. mute/unmute (video)
    3. change background
    4. refresh imports

The menu header shows the screen's connector name (HDMI-1, DP-2) so you know which display you're acting on.

Usage:
    ./aurora.py                       # daemonize; default image on every screen
    ./aurora.py FILE0 FILE1 [...]     # one file per monitor, in order
    ./aurora.py --mode fit clip.mp4   # fill|fit|stretch  (default: fill)
    ./aurora.py --audio clip.mp4      # start videos unmuted
    ./aurora.py --foreground ...      # don't background the process
    ./aurora.py --stop                # stop the running background instance
    ./aurora.py --install-autostart   # run on login (no files -> default image)
    ./aurora.py --remove-autostart

Ship wallpaper-default.jpg next to this script; the following directories are created on run if they do not exist:
    1. workspace/
    2. .upload/
    3. images/
    4. videos/
    5. .trash/
Logs go to .aurora.log.
"""

import argparse
import atexit
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import warnings

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_IMAGE = os.path.join(SCRIPT_DIR, "wallpaper-default.jpg")
WORKSPACE_DIR = os.path.join(SCRIPT_DIR, "workspace")
LOG_FILE = os.path.join(SCRIPT_DIR, ".aurora.log")
STATE_FILE = os.path.join(SCRIPT_DIR, ".aurora-state.json")

WALLPAPER_PREFIX = "wallpaper-"
TRASH_MAX_AGE_DAYS = 7

_logfile = None

def is_video(path):
    return os.path.splitext(path)[1].lower() in VIDEO_EXTS

def detect_wm():
    """Best-effort window-manager id: 'i3', 'cinnamon', or whatever
    _NET_WM_NAME reports (lowercased). Used to adapt behaviour per WM."""
    for var in ("XDG_CURRENT_DESKTOP", "DESKTOP_SESSION", "XDG_SESSION_DESKTOP"):
        value = os.environ.get(var, "").lower()
        if "i3" in value:
            return "i3"
        if "cinnamon" in value:
            return "cinnamon"
    # Ask the WM itself (works when env vars are unset, e.g. bare startx).
    try:
        out = subprocess.run(["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"],
                             capture_output=True, text=True, timeout=3).stdout
        match = re.search(r"0x[0-9a-fA-F]+", out)
        if match:
            out = subprocess.run(["xprop", "-id", match.group(0), "_NET_WM_NAME"],
                                 capture_output=True, text=True, timeout=3).stdout
            name = out.split("=", 1)[-1].strip().strip('"').lower()
            if name:
                return name
    except Exception:
        pass
    return "unknown"

def monitor_connectors():
    """Parse `xrandr --listmonitors` into [(name, x, y, w, h), ...]."""
    try:
        out = subprocess.run(["xrandr", "--listmonitors"],
                             capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return []
    result = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4 or not parts[0].rstrip(":").isdigit():
            continue
        name = parts[-1]
        for part in parts:
            match = re.match(r"(\d+)/\d+x(\d+)/\d+\+(-?\d+)\+(-?\d+)$", part)
            if match:
                width, height, x, y = map(int, match.groups())
                result.append((name, x, y, width, height))
                break
    return result


def connector_for(connectors, localSpace, idx):
    for (name, x, y, width, height) in connectors:
        if x == localSpace.x and y == localSpace.y:
            return name
    if idx < len(connectors):
        return connectors[idx][0]
    return f"screen-{idx}"

class Workspace:
    """.upload/ + images/ + videos/ + .trash/ next to the script.

    Import: recursively pull "wallpaper-*" files out of .upload/ (structure ignored) into images/ or videos/ by extension, 
    then move every leftover top-level item into .trash/ so .upload/ ends up empty. Trash self-expires
    after TRASH_MAX_AGE_DAYS, checked at startup and after each import.
    """

    def __init__(self, root):
        self.root = root

        self.upload = os.path.join(root, ".upload")
        self.images = os.path.join(root, "images")
        self.videos = os.path.join(root, "videos")
        self.trash = os.path.join(root, ".trash")

        legacy = os.path.join(root, "upload")

        if os.path.isdir(legacy) and not os.path.exists(self.upload):
            try:
                os.rename(legacy, self.upload)
            except OSError:
                pass
        for directory in (self.upload, self.images, self.videos, self.trash):
            os.makedirs(directory, exist_ok=True)

    @staticmethod
    def _unique(folder, name):
        base, extension = os.path.splitext(name)
        candidate, idx = name, 1
        while os.path.exists(os.path.join(folder, candidate)):
            candidate = f"{base} ({idx}){extension}"
            idx += 1
        return os.path.join(folder, candidate)

    def copy_into_upload(self, folders):
        """Copy each source folder into .upload/, then prefix its top-level
        image/video files with "wallpaper-". Returns (copied, prefixed)."""
        copied = prefixed = 0
        for source in folders:
            if not os.path.isdir(source):
                continue
            name = os.path.basename(os.path.normpath(source))
            destination = self._unique(self.upload, name)

            shutil.copytree(source, destination)

            prefixed += self.prefix_wallpapers(destination)
            copied += 1
        return copied, prefixed

    def prefix_wallpapers(self, folder):
        """Rename top-level image/video files in uploaded folder(s) to start with the
        "wallpaper-" prefix, skipping ones already prefixed. Returns count of prefixed files."""
        renamed = 0
        for fileName in os.listdir(folder):
            source = os.path.join(folder, fileName)
            if not os.path.isfile(source):
                continue
            if fileName.lower().startswith(WALLPAPER_PREFIX):
                continue
            extension = os.path.splitext(fileName)[1].lower()
            if extension not in IMAGE_EXTS and extension not in VIDEO_EXTS:
                continue
            destination = self._unique(folder, WALLPAPER_PREFIX + fileName)
            os.rename(source, destination)
            renamed += 1
        return renamed

    def import_uploads(self):
        """Returns (images_moved, videos_moved, items_trashed)."""
        self.prefix_wallpapers(self.upload)
        moved_img = moved_vid = 0
        for dirpath, _dirs, files in os.walk(self.upload):
            for fileName in files:
                if not fileName.lower().startswith(WALLPAPER_PREFIX):
                    continue
                extension = os.path.splitext(fileName)[1].lower()
                source = os.path.join(dirpath, fileName)
                if extension in IMAGE_EXTS:
                    shutil.move(source, self._unique(self.images, fileName))
                    moved_img += 1
                elif extension in VIDEO_EXTS:
                    shutil.move(source, self._unique(self.videos, fileName))
                    moved_vid += 1
        trashed = 0
        stamp = int(time.time())
        for entry in os.listdir(self.upload):
            source = os.path.join(self.upload, entry)
            shutil.move(source, self._unique(self.trash, f"{stamp}__{entry}"))
            trashed += 1
        return moved_img, moved_vid, trashed

    def clean_trash(self, max_age_days=TRASH_MAX_AGE_DAYS):
        cutoff = time.time() - max_age_days * 86400
        removed = 0
        if not os.path.isdir(self.trash):
            return 0
        for entry in os.listdir(self.trash):
            path = os.path.join(self.trash, entry)
            match = re.match(r"(\d+)__", entry)
            if match:
                trash = int(match.group(1))
            else:
                try:
                    trash = int(os.path.getmtime(path))
                except OSError:
                    continue
            if trash >= cutoff:
                continue
            try:
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                removed += 1
            except OSError:
                pass
        return removed

def autostart_path():
    return os.path.join(
        os.path.expanduser("~/.config/autostart"), "aurora.desktop")

_AUTOSTART_BEGIN = "# >>> aurora-lite autostart >>>"
_AUTOSTART_END = "# <<< aurora-lite autostart <<<"

def _autostart_command(files, mode, audio, dim):
    """Shell command that launches aurora with the given options."""
    script = os.path.abspath(__file__)
    python = sys.executable or "/usr/bin/python3"
    parts = [python, script]
    if mode and mode != "fill":
        parts += ["--mode", mode]
    if audio:
        parts += ["--audio"]
    if dim and dim > 0:
        parts += ["--dim", str(dim)]
    parts += [os.path.abspath(f) for f in files]
    return " ".join(shlex.quote(p) for p in parts)

def _tiling_config_path(wm):
    """Config file to edit for a tiling WM, or None if unknown format."""
    if "sway" in wm:
        candidates = ["~/.config/sway/config"]
    elif "i3" in wm:
        candidates = ["~/.config/i3/config", "~/.i3/config"]
    else:
        return None
    for p in candidates:
        expanded = os.path.expanduser(p)
        if os.path.exists(expanded):
            return expanded
    return os.path.expanduser(candidates[0])

def _strip_autostart_block(text):
    """Remove any existing aurora-managed exec block from config text."""
    out, skipping = [], False
    for line in text.splitlines():
        if line.strip() == _AUTOSTART_BEGIN:
            skipping = True
            continue
        if line.strip() == _AUTOSTART_END:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    return "\n".join(out)

def _install_into_config(config_path, cmd):
    """Idempotently write the aurora exec block into an i3/sway config."""
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    try:
        text = open(config_path).read() if os.path.exists(config_path) else ""
    except OSError:
        text = ""
    text = _strip_autostart_block(text).rstrip("\n")
    block = (f"\n\n{_AUTOSTART_BEGIN}\n"
             f"exec --no-startup-id {cmd}\n"
             f"{_AUTOSTART_END}\n")
    with open(config_path, "w") as f:
        f.write(text + block)
    return config_path

def _install_xdg(files, mode, audio, dim):
    script = os.path.abspath(__file__)
    python = sys.executable or "/usr/bin/python3"
    argv = [python, script]
    if mode != "fill":
        argv += ["--mode", mode]
    if audio:
        argv += ["--audio"]
    if dim and dim > 0:
        argv += ["--dim", str(dim)]
    argv += [os.path.abspath(file) for file in files]
    exec_line = " ".join(f'"{arg}"' if " " in arg else arg for arg in argv)

    destination = autostart_path()
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    with open(destination, "w") as f:
        f.write(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=aurora\n"
            "Comment=Per-monitor wallpaper manager\n"
            f"Exec={exec_line}\n"
            "X-GNOME-Autostart-enabled=true\n"
            "X-GNOME-Autostart-Delay=2\n"
            "NoDisplay=true\n")
    return destination

def install_autostart(files, mode, audio, dim=0.0):
    """Install login autostart, adapting to the detected window manager."""
    wm = detect_wm()
    config = _tiling_config_path(wm)
    if config is not None:
        return _install_into_config(config, _autostart_command(files, mode, audio, dim))
    if any(t in wm for t in ("bspwm", "awesome", "dwm", "xmonad", "herbstluftwm")):
        cmd = _autostart_command(files, mode, audio, dim)
        print(f"aurora: {wm}: can't auto-edit that WM's config. Add this to "
              f"your startup manually:\n  {cmd}", file=sys.stderr)
        return None
    return _install_xdg(files, mode, audio, dim)

def remove_autostart():
    """Remove aurora autostart from XDG and any i3/sway config."""
    removed = []
    dest = autostart_path()
    if os.path.exists(dest):
        try:
            os.remove(dest)
            removed.append(dest)
        except OSError:
            pass
    for p in ("~/.config/i3/config", "~/.i3/config", "~/.config/sway/config"):
        expanded = os.path.expanduser(p)
        if not os.path.exists(expanded):
            continue
        try:
            text = open(expanded).read()
            stripped = _strip_autostart_block(text)
            if stripped != text:
                with open(expanded, "w") as f:
                    f.write(stripped.rstrip("\n") + "\n")
                removed.append(expanded)
        except OSError:
            pass
    return removed or None

def load_state():
    """Return {connector: {"path": str, "dim": float}} per screen.
    Migrates the old {connector: path_string} format transparently."""
    try:
        with open(STATE_FILE) as file:
            data = json.load(file)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    result = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[str(key)] = {"path": value, "dim": 0.0}
        elif isinstance(value, dict) and value.get("path"):
            try:
                dim = float(value.get("dim", 0.0))
            except (TypeError, ValueError):
                dim = 0.0
            result[str(key)] = {"path": str(value["path"]),
                                "dim": min(max(dim, 0.0), 1.0)}
    return result

def save_state(state):
    """Persist {connector: {path, dim}} atomically (temp file + rename)."""
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass

def pid_file():
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(base, "aurora.pid")

def read_pid():
    try:
        with open(pid_file()) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None

def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True

def write_pid():
    with open(pid_file(), "w") as f:
        f.write(str(os.getpid()))

def remove_pid():
    if read_pid() == os.getpid():
        try:
            os.remove(pid_file())
        except OSError:
            pass

def stop_daemon():
    pid = read_pid()
    if pid is None:
        print("aurora: not running")
        return
    if not pid_alive(pid):
        print("aurora: stale pid file, cleaning up")
        try:
            os.remove(pid_file())
        except OSError:
            pass
        return
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            if not pid_alive(pid):
                break
            time.sleep(0.1)
        else:
            os.kill(pid, signal.SIGKILL)
        print(f"aurora: stopped (pid {pid})")
    except OSError as errno:
        print(f"aurora: could not stop pid {pid}: {errno}")

def daemonize(log_path):
    """Double-fork into the background; send stdout/stderr to a log file."""
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)

    sys.stdout.flush()
    sys.stderr.flush()

    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, sys.stdin.fileno())
    os.close(devnull)

    logFile = open(log_path, "a", buffering=1)
    os.dup2(logFile.fileno(), sys.stdout.fileno())
    os.dup2(logFile.fileno(), sys.stderr.fileno())

    return logFile


def _gui_main(args, files):
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')
    gi.require_version('GdkX11', '3.0')
    gi.require_version('GdkPixbuf', '2.0')
    gi.require_version('Gst', '1.0')
    gi.require_version('GstVideo', '1.0')
    from gi.repository import (Gtk, Gdk, 
                               GdkX11, GdkPixbuf,
                               Gst, GstVideo, GLib)
    import cairo

    try:
        gi.require_version('Wnck', '3.0')
        from gi.repository import Wnck
    except (ValueError, ImportError):
        Wnck = None
        print("aurora: libwnck not found (install gir1.2-wnck-3.0) - "
              "auto-pause on maximized windows disabled", file=sys.stderr)

    class _DesktopWindow(Gtk.Window):
        """Shared wallpaper-window behaviour: below everything, pinned to one
        monitor, right-click menu."""

        def __init__(self, geometry):
            super().__init__()
            self.geometry = geometry
            self.connector = None 
            self.manager = None

            self.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
            self.set_decorated(False)
            self.set_resizable(False)
            self.set_skip_taskbar_hint(True)
            self.set_skip_pager_hint(True)
            self.set_keep_below(True)
            self.set_app_paintable(True)
            self.stick()

            self.set_default_size(geometry.width, geometry.height)
            self.move(geometry.x, geometry.y)

            self.connect("realize", self._on_realize)
            self.connect("map", lambda w: self.move(geometry.x, geometry.y))

        def _on_realize(self, _w):
            gdkwin = self.get_window()
            if TILING_WM:
                try:
                    gdkwin.set_override_redirect(True)
                except Exception as e:
                    print(f"aurora: could not set override-redirect: {e}",
                          file=sys.stderr)
            GLib.timeout_add(50, self._reposition)
        def _reposition(self):
            self.move(self.geometry.x, self.geometry.y)
            self.resize(self.geometry.width, self.geometry.height)
            if TILING_WM:
                gdkwin = self.get_window()
                if gdkwin is not None:
                    gdkwin.lower()
            return False

        def _attach_input(self, widget):
            widget.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            widget.connect("button-press-event", self._on_button_press)

        def _on_button_press(self, _w, event):
            if event.button == 3: 
                self._popup_menu(event)
                return True
            return False

        def _popup_menu(self, event):
            menu = Gtk.Menu()

            header = Gtk.MenuItem(label=f"Display: {self.connector}")
            header.set_sensitive(False)

            menu.append(header)
            menu.append(Gtk.SeparatorMenuItem())

            self._populate_menu(menu)

            change = Gtk.MenuItem(label="Change background\u2026")
            change.connect("activate", self._on_change_background)
            menu.append(change)

            upload = Gtk.MenuItem(label="Upload folders\u2026")
            upload.connect("activate", self._on_upload)
            menu.append(upload)

            refresh = Gtk.MenuItem(label="Refresh")
            refresh.connect("activate", lambda *_: self.manager.refresh(self))
            menu.append(refresh)

            dim_item = Gtk.MenuItem(label="Dim\u2026")
            dim_item.connect("activate", self._open_dim_dialog)
            menu.append(dim_item)

            menu.append(Gtk.SeparatorMenuItem())

            quit_item = Gtk.MenuItem(label="Quit aurora")
            quit_item.connect("activate", lambda *_: self.manager.quit())
            menu.append(quit_item)

            menu.show_all()
            menu.popup_at_pointer(event)

        def _open_dim_dialog(self, _item):
            win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            win.set_title("Dim")
            win.set_type_hint(Gdk.WindowTypeHint.DIALOG)
            win.set_keep_above(True)
            win.set_resizable(False)
            win.set_default_size(280, 70)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                          spacing=6, margin=10)
            box.pack_start(
                Gtk.Label(label=f"Dim \u2014 {self.connector}"), False, False, 0)
            scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.0, 1.0, 0.05)
            scale.set_value(getattr(self, "dim", 0.0))
            scale.set_draw_value(True)
            scale.set_value_pos(Gtk.PositionType.RIGHT)
            scale.connect("value-changed", self._on_dim_changed)
            box.pack_start(scale, True, True, 0)
            win.add(box)
            win.show_all()

        def _on_dim_changed(self, scale):
            self.set_dim(scale.get_value())
            if getattr(self, "_dim_save_timer", 0):
                GLib.source_remove(self._dim_save_timer)
            self._dim_save_timer = GLib.timeout_add(400, self._save_dim)

        def _save_dim(self):
            self._dim_save_timer = 0
            self.manager.remember(self.connector, self.path, self.dim)
            return False

        def set_dim(self, value):
            pass

        def _populate_menu(self, menu):
            pass

        def _on_change_background(self, _item):
            dialog = Gtk.FileChooserNative.new("Choose wallpaper", 
                                               self, Gtk.FileChooserAction.OPEN,
                                               "_Open", "_Cancel")
            media = Gtk.FileFilter()
            media.set_name("Images & video")
            for extension in IMAGE_EXTS | VIDEO_EXTS:
                media.add_pattern(f"*{extension}")
            dialog.add_filter(media)
            allFiles = Gtk.FileFilter()
            allFiles.set_name("All files")
            allFiles.add_pattern("*")
            dialog.add_filter(allFiles)

            workspace = getattr(self.manager, "workspace", None)
            if workspace is not None:
                dialog.set_current_folder(workspace.root)

            if dialog.run() == Gtk.ResponseType.ACCEPT:
                path = dialog.get_filename()
                dialog.destroy()
                if path:
                    self.manager.replace(self, path)
            else:
                dialog.destroy()

        def _on_upload(self, _item):
            dialog = Gtk.FileChooserNative.new("Select folders to upload", 
                                               self, Gtk.FileChooserAction.SELECT_FOLDER, 
                                               "_Upload", "_Cancel")
            dialog.set_select_multiple(True)
            if dialog.run() == Gtk.ResponseType.ACCEPT:
                folders = dialog.get_filenames()
                dialog.destroy()
                self.manager.upload_and_sort(self, folders)
            else:
                dialog.destroy()

        def start(self):
            pass

        def stop(self):
            pass

        def set_covered(self, covered):
            pass

    class ImageWallpaper(_DesktopWindow):
        def __init__(self, geometry, path, mode="fill", dim=0.0):
            super().__init__(geometry)
            self.path = path
            self.mode = mode
            self.dim = dim
            self.pixelBuffer = None
            self._anim_iter = None
            self._anim_timer = 0

            self._load(path)

            self.area = Gtk.DrawingArea()
            self.area.connect("draw", self._on_draw)
            self.add(self.area)
            self._attach_input(self.area)

        def set_dim(self, value):
            self.dim = min(max(value, 0.0), 1.0)
            if self.get_realized():
                self.area.queue_draw()

        def _load(self, path):
            """Load as an animation. Single-frame files paint once (0% idle);
            multi-frame files (animated gif/webp) drive a frame timer."""
            try:
                anim = GdkPixbuf.PixbufAnimation.new_from_file(path)
            except GLib.Error as e:
                print(f"aurora: cannot load '{path}': {e.message}", file=sys.stderr)
                if path != DEFAULT_IMAGE:
                    self._load(DEFAULT_IMAGE)
                return
            if anim.is_static_image():
                self.pixelBuffer = anim.get_static_image()
                return

            self._anim_iter = anim.get_iter(None)
            self.pixelBuffer = self._anim_iter.get_pixbuf()
            self._schedule_next_frame()

        def _schedule_next_frame(self):
            delay = self._anim_iter.get_delay_time()
            if delay < 0:
                delay = 100
            delay = max(delay, 20)
            self._anim_timer = GLib.timeout_add(delay, self._advance_frame)

        def _advance_frame(self):
            self._anim_timer = 0
            self._anim_iter.advance(None)        

            self.pixelBuffer = self._anim_iter.get_pixbuf()
            if self.get_realized():
                self.area.queue_draw()
            self._schedule_next_frame()
            return False

        def stop(self):
            if self._anim_timer:
                GLib.source_remove(self._anim_timer)
                self._anim_timer = 0

        def _on_draw(self, widget, context):
            alloc = widget.get_allocation()
            width, height = alloc.width, alloc.height
            if self.pixelBuffer is None:
                context.set_source_rgb(0, 0, 0)
                context.paint()
                if self.dim > 0:
                    context.identity_matrix()
                    context.set_source_rgba(0, 0, 0, self.dim)
                    context.paint()
                return False
            pixelBufferWidth, pixelBufferHeight = self.pixelBuffer.get_width(), self.pixelBuffer.get_height()
            if pixelBufferWidth == 0 or pixelBufferHeight == 0:
                return False
            context.set_source_rgb(0, 0, 0)
            context.paint()
            if self.mode == "stretch":
                stretchedX, stretchedY, generalX, generalY = width / pixelBufferWidth, height / pixelBufferHeight, 0.0, 0.0
            else:
                pick = max if self.mode == "fill" else min
                selection = pick(width / pixelBufferWidth, height / pixelBufferHeight)
                stretchedX = stretchedY = selection
                generalX = (width - pixelBufferWidth * selection) / 2.0
                generalY = (height - pixelBufferHeight * selection) / 2.0
            context.translate(generalX, generalY)
            context.scale(stretchedX, stretchedY)
            Gdk.cairo_set_source_pixbuf(context, self.pixelBuffer, 0, 0)
            context.get_source().set_filter(cairo.FILTER_BILINEAR)
            context.paint()
            return False

    class VideoWallpaper(_DesktopWindow):
        def __init__(self, geometry, path, mode="fill", audio=False, dim=0.0):
            super().__init__(geometry)
            self.path = path
            self.mode = mode
            self.dim = dim

            self._xid = None
            self._manual_paused = False 
            self._covered = False 
            self._muted = not audio

            self.area = Gtk.DrawingArea()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore") 
                self.area.set_double_buffered(False)
            self.area.connect("realize", self._on_area_realize)
            self.add(self.area)
            self._attach_input(self.area)

            self._build_pipeline()

        def _on_area_realize(self, _w):
            self._xid = self.area.get_window().get_xid()

        def _build_pipeline(self):
            self.pipeline = Gst.ElementFactory.make("playbin", None)
            if not self.pipeline:
                sys.exit("aurora: 'playbin' missing (install gstreamer1.0-plugins-base)")
            self.pipeline.set_property("uri", Gst.filename_to_uri(self.path))

            sink = Gst.ElementFactory.make("glimagesink", None)
            if sink is not None:
                sink.set_property("force-aspect-ratio", self.mode != "stretch")
                self.pipeline.set_property("video-sink", sink)

            self.pipeline.set_property("mute", self._muted)

            self._balance = None
            if self.dim > 0:
                try:
                    flt = Gst.parse_bin_from_description(
                        "glupload ! glcolorbalance name=auroradim", True)
                    self._balance = flt.get_by_name("auroradim")
                    if self._balance is not None:
                        self._balance.set_property(
                            "brightness", -min(max(self.dim, 0.0), 1.0))
                    self.pipeline.set_property("video-filter", flt)
                except Exception as e:
                    print(f"aurora: dim filter unavailable: {e}", file=sys.stderr)

            bus = self.pipeline.get_bus()
            bus.add_signal_watch()
            bus.enable_sync_message_emission()
            bus.connect("sync-message::element", self._on_sync_message)
            bus.connect("message::eos", self._on_eos)
            bus.connect("message::error", self._on_error)

        def _on_sync_message(self, _bus, msg):
            structure = msg.get_structure()
            if structure and structure.get_name() == "prepare-window-handle" and self._xid:
                sink = msg.src
                sink.set_window_handle(self._xid)
                sink.set_render_rectangle(0, 0, self.geometry.width, self.geometry.height)
                try:
                    sink.handle_events(False)
                except Exception:
                    pass

        def _on_eos(self, _bus, _msg):
            self.pipeline.seek_simple(Gst.Format.TIME,
                                      Gst.SeekFlags.FLUSH, 
                                      0)

        def _on_error(self, _bus, msg):
            errno, dbg = msg.parse_error()
            print(f"aurora: gstreamer error: {errno.message}", file=sys.stderr)
            if dbg:
                print(dbg, file=sys.stderr)

        def _populate_menu(self, menu):
            playing = Gtk.MenuItem(label="Play" if self._manual_paused else "Pause")
            playing.connect("activate", self._toggle_playpause)
            menu.append(playing)

            muted = Gtk.MenuItem(label="Unmute" if self._muted else "Mute")
            muted.connect("activate", self._toggle_mute)
            menu.append(muted)

            menu.append(Gtk.SeparatorMenuItem())

        def _toggle_playpause(self, _):
            self._manual_paused = not self._manual_paused
            self._apply_playstate()

        def _toggle_mute(self, _):
            self._muted = not self._muted
            self.set_muted(self._muted)

        def _apply_playstate(self):
            play = not self._manual_paused and not self._covered
            self.pipeline.set_state(Gst.State.PLAYING if play else Gst.State.PAUSED)

        def set_covered(self, covered):
            if covered == self._covered:
                return
            self._covered = covered
            self._apply_playstate()

        def start(self):
            self._apply_playstate()

        def stop(self):
            self.pipeline.set_state(Gst.State.NULL)

        def pause(self):
            self.pipeline.set_state(Gst.State.PAUSED)

        def play(self):
            self.pipeline.set_state(Gst.State.PLAYING)

        def set_dim(self, value):
            self.dim = min(max(value, 0.0), 1.0)
            if self._balance is not None:
                self._balance.set_property("brightness", -self.dim)

        def set_muted(self, muted):
            self.pipeline.set_property("mute", muted)


    class Manager:
        """Owns the set of windows; can swap one screen's wallpaper live."""

        def __init__(self, mode, audio, workspace=None, state=None, dim=0.0):
            self.mode = mode
            self.audio = audio
            self.workspace = workspace
            self.state = state if state is not None else {}
            self.default_dim = min(max(dim, 0.0), 1.0)
            self.windows = []
            self._covered_set = None

        def remember(self, connector, path, dim):
            """Persist a screen's wallpaper path + dim together."""
            if not connector:
                return
            self.state[connector] = {"path": path, "dim": round(float(dim), 3)}
            save_state(self.state)

        def set_covered(self, covered):
            """covered: set of connector names currently covered."""
            if covered == self._covered_set:
                return
            self._covered_set = set(covered)
            print(f"aurora: covered screens -> {sorted(covered) or []}",
                  file=sys.stderr)
            for window in self.windows:
                window.set_covered(window.connector in covered)

        def _show_summary(self, parent, lines):
            dlg = Gtk.MessageDialog(
                transient_for=parent, modal=True,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK, text="aurora")
            dlg.format_secondary_text("\n".join(lines))
            dlg.run()
            dlg.destroy()

        def _sort_and_lines(self):
            """Run the upload sort + trash cleanup; return summary lines."""
            uploadedImages, uploadedVideos, uploadedLeftover = self.workspace.import_uploads()
            removed = self.workspace.clean_trash()
            if uploadedImages == 0 and uploadedVideos == 0 and uploadedLeftover == 0:
                lines = ["Nothing new in .upload/ to import."]
            else:
                lines = [f"Imported {uploadedImages + uploadedVideos} wallpaper(s): "
                         f"{uploadedImages} image(s), {uploadedVideos} video(s).",
                         f"Moved {uploadedLeftover} leftover item(s) to trash."]
            if removed:
                lines.append(f"Cleaned {removed} expired item(s) from trash.")
            return lines

        def refresh(self, parent):
            """Re-scan .upload/, sort new content, and report."""
            if self.workspace is None:
                return
            self._show_summary(parent, self._sort_and_lines())

        def upload_and_sort(self, parent, folders):
            """Copy picked folders into .upload/, then sort and report."""
            if self.workspace is None or not folders:
                return
            copied, prefixed = self.workspace.copy_into_upload(folders)
            lines = [f"Copied {copied} folder(s) into .upload/."]
            if prefixed:
                lines.append(f"Renamed {prefixed} file(s) with the "
                             f"wallpaper- prefix.")
            lines += self._sort_and_lines()
            self._show_summary(parent, lines)

        def build(self, geo, connector, path, dim=0.0):
            if not path or not os.path.exists(path):
                path = DEFAULT_IMAGE
            if is_video(path):
                window = VideoWallpaper(geo, path, self.mode, self.audio, dim)
            else:
                window = ImageWallpaper(geo, path, self.mode, dim)
            window.connector = connector
            window.manager = self
            return window

        def replace(self, old, path):
            geometry, connector = old.geometry, old.connector
            dim = getattr(old, "dim", self.default_dim)
            old.stop()
            old.destroy()
            if old in self.windows:
                self.windows.remove(old)

            new = self.build(geometry, connector, path, dim)
            self.windows.append(new)
            new.show_all()
            if self._covered_set is not None:
                new.set_covered(connector in self._covered_set)
            GLib.idle_add(self._start_one, new)

            self.remember(connector, path, dim)

        @staticmethod
        def _start_one(window):
            window.start()
            return False

        def quit(self):
            for window in list(self.windows):
                window.stop()
            Gtk.main_quit()

    class CoverageWatcher:
        """Pause a screen's video when a maximized/fullscreen window covers it.

        Event-driven: recomputes only when a window's state changes, a window
        opens/closes, focus moves, or the workspace switches - never on a
        timer, so idle cost is essentially nil.

        On tiling WMs (i3) "maximized" is meaningless - every tiled window
        fills its container - so only true fullscreen counts there.
        """

        TILING_WMS = ("i3", "sway", "bspwm", "awesome", "dwm", "xmonad", "herbstluftwm")

        def __init__(self, manager, monitors, wm="unknown"):
            self.manager = manager
            self.monitors = monitors
            self.tiling = any(t in wm for t in self.TILING_WMS)

            self.screen = Wnck.Screen.get_default()
            self.screen.force_update()
            self.screen.connect("active-window-changed", self._recompute)
            self.screen.connect("window-opened", self._on_opened)
            self.screen.connect("window-closed", self._recompute)
            self.screen.connect("active-workspace-changed", self._recompute)

            for window in self.screen.get_windows():
                window.connect("state-changed", self._recompute)
            self.update()

        def _on_opened(self, _screen, window):
            window.connect("state-changed", self._recompute)
            self.update()

        def _recompute(self, *_):
            self.update()

        def update(self):
            covered = set()
            activeWorkspace = self.screen.get_active_workspace()
            skip = (Wnck.WindowType.DESKTOP, Wnck.WindowType.DOCK,
                    Wnck.WindowType.MENU, Wnck.WindowType.SPLASHSCREEN)
            for window in self.screen.get_windows():
                if window.is_minimized():
                    continue
                if window.get_window_type() in skip:
                    continue
                if (activeWorkspace is not None and not window.is_pinned()
                        and not window.is_on_workspace(activeWorkspace)):
                    continue
                if self.tiling:
                    if not window.is_fullscreen():
                        continue
                elif not (window.is_maximized() or window.is_fullscreen()):
                    continue
                x, y, windowWidth, windowHeight = window.get_geometry()
                generalX, generalY = x + windowWidth // 2, y + windowHeight // 2
                for (connector, monitorX, monitorY, monitorWidth, monitorHeight) in self.monitors:
                    if monitorX <= generalX < monitorX + monitorWidth and monitorY <= generalY < monitorY + monitorHeight:
                        covered.add(connector)
                        break
            self.manager.set_covered(covered)

    Gst.init(None)

    for name in ("nvh264dec", "nvh264sldec", "nvvp9dec", "nvav1dec"):
        factory = Gst.ElementFactory.find(name)
        if factory:
            factory.set_rank(Gst.Rank.PRIMARY + 1)

    workspace = Workspace(WORKSPACE_DIR)
    workspace.clean_trash()

    wm = detect_wm()
    print(f"aurora: window manager detected: {wm}", file=sys.stderr)

    TILING_WM = any(t in wm for t in ("i3", "sway", "bspwm", "awesome", "dwm", "xmonad", "herbstluftwm"))

    display = Gdk.Display.get_default()
    if display is None:
        sys.exit("aurora: no display (are you on an X session?)")
    numberOfMonitors = display.get_n_monitors()
    connectors = monitor_connectors()

    state = load_state()
    manager = Manager(args.mode, args.audio, workspace, state, args.dim)

    def on_sigterm(*_):
        manager.quit()

    cli = list(args.files)
    monitorRectangles = []
    for idx in range(numberOfMonitors):
        geometry = display.get_monitor(idx).get_geometry()
        connector = connector_for(connectors, geometry, idx)
        monitorRectangles.append((connector, geometry.x, geometry.y, geometry.width, geometry.height))
        entry = state.get(connector)
        if cli:
            path = cli[idx] if idx < len(cli) else cli[-1]
            dim = entry["dim"] if entry else args.dim
            state[connector] = {"path": path, "dim": round(float(dim), 3)}
        elif entry:
            path, dim = entry["path"], entry["dim"]
        else:
            path, dim = None, args.dim
        window = manager.build(geometry, connector, path, dim)
        window.show_all()
        manager.windows.append(window)

    if cli:
        save_state(state)

    watcher = None
    if Wnck is not None:
        watcher = CoverageWatcher(manager, monitorRectangles, wm)  # noqa: F841

    def start_all():
        for window in manager.windows:
            window.start()
        return False
    GLib.idle_add(start_all)

    signal.signal(signal.SIGTERM, on_sigterm)
    signal.signal(signal.SIGINT, on_sigterm)
    Gtk.main()

def main():
    argumentParser = argparse.ArgumentParser(description="Per-monitor wallpaper manager")
    argumentParser.add_argument("files", nargs="*",
                                help="image/video path(s), per monitor "
                                "(default: wallpaper-default.jpg on every screen)")
    argumentParser.add_argument("--mode", choices=["fill", "fit", "stretch"], default="fill")
    argumentParser.add_argument("--audio", action="store_true", help="start videos unmuted")
    argumentParser.add_argument("--stop", action="store_true",
                                help="stop the running background instance, then exit")
    argumentParser.add_argument("--restart", action="store_true",
                                help="stop the running instance and start a fresh one")
    argumentParser.add_argument("--install-autostart", action="store_true",
                                help="write ~/.config/autostart entry, then exit")
    argumentParser.add_argument("--remove-autostart", action="store_true",
                                help="remove the autostart entry, then exit")
    argumentParser.add_argument("--foreground", action="store_true",
                                help="run in the foreground instead of daemonizing")
    argumentParser.add_argument("--dim", type=float, default=0.0,
                                help="darken wallpapers 0.0-1.0 for readability (e.g. 0.35)")
    args = argumentParser.parse_args()

    if args.stop:
        stop_daemon()
        return
    if args.restart:
        stop_daemon()
        pid = read_pid()
        if pid is not None and not pid_alive(pid):
            try:
                os.remove(pid_file())
            except OSError:
                pass
    if args.remove_autostart:
        removed = remove_autostart()
        if removed:
            print(f"aurora: autostart removed -> {', '.join(removed)}")
        else:
            print("aurora: no autostart entry to remove")
        return
    if args.install_autostart:
        result = install_autostart(args.files, args.mode, args.audio, args.dim)
        if result:
            print(f"aurora: autostart installed -> {result}")
        else:
            print("aurora: see instructions above")
        return

    if not os.path.exists(DEFAULT_IMAGE):
        sys.exit(f"aurora: default image missing: {DEFAULT_IMAGE} "
                 "(bundle wallpaper-default.jpg next to the script)")
    for file in args.files:
        if not os.path.exists(file):
            sys.exit(f"aurora: file not found: {file}")
    files = args.files if args.files else [DEFAULT_IMAGE]

    existing = read_pid()
    if existing and pid_alive(existing):
        sys.exit(f"aurora: already running (pid {existing}); use --stop")

    if not args.foreground:
        global _logfile
        _logfile = daemonize(LOG_FILE)
    write_pid()
    atexit.register(remove_pid)

    _gui_main(args, files)

if __name__ == "__main__":
    main()
