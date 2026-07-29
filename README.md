# Aurora Lite

A lightweight per-monitor animated wallpaper engine for **Linux Mint Cinnamon**.

Aurora Lite allows you to run **images and videos as desktop wallpapers**, with support for multiple monitors, per-screen wallpapers, right-click controls, video playback, and automatic pause when a window covers the wallpaper.

<img src="wallpaper-default.jpg" width="600">

## Features

- 🖥️ Per-monitor wallpaper support
- 🎞️ Animated video wallpapers
- 🖼️ Static image wallpapers
- 🖱️ Right-click desktop controls
- 🔊 Optional video audio playback
- ⏯️ Play / pause video wallpapers
- 🔇 Video mute / unmute
- 🔄 Live wallpaper switching
- 📂 Wallpaper import system
- 🚀 Cinnamon autostart support
- 💾 Remembers the last wallpaper used per monitor
- 💤 Automatically pauses videos when a maximized/fullscreen window covers the screen

---

# Installation

## Requirements

Aurora Lite currently targets:

- Linux Mint
- Cinnamon desktop
- X11 session

Wayland support is currently not available.

---

## Dependencies

Install the required system packages:

```bash
sudo apt update

sudo apt install \
python3 \
python3-gi \
gir1.2-gtk-3.0 \
gir1.2-gdkpixbuf-2.0 \
gir1.2-gst-plugins-base-1.0 \
gir1.2-gstreamer-1.0 \
gir1.2-wnck-3.0 \
gstreamer1.0-tools \
gstreamer1.0-plugins-base \
gstreamer1.0-plugins-good \
gstreamer1.0-plugins-bad \
gstreamer1.0-libav \
x11-xserver-utils
```

### What these packages provide

| Package | Purpose |
|---|---|
| python3 | Python runtime |
| python3-gi | Python GObject bindings |
| GTK 3 | Desktop windows and menus |
| GdkPixbuf | Image loading |
| GStreamer | Video playback engine |
| Wnck | Detect maximized/fullscreen windows |
| xrandr | Monitor detection |

---

# Getting Started

## 1. Clone the repository

Choose a location for Aurora Lite:

```bash
cd ~
```

Clone the project:

```bash
git clone https://github.com/ghosthookcc/aurora-lite.git
```

Enter the project directory:

```bash
cd aurora-lite
```

Your folder should contain:

```
aurora-lite/
├── aurora.py
├── wallpaper-default.jpg
└── README.md
```

`wallpaper-default.jpg` is included with the project and is used automatically when no wallpaper is selected.

---

# First Run

Make the script executable:

```bash
chmod +x aurora.py
```

Start Aurora Lite:

```bash
./aurora.py --foreground
```

The default wallpaper will now appear on all connected monitors.

To run Aurora Lite in the background:

```bash
./aurora.py
```

Logs are written to:

```
.aurora.log
```

---

# Usage

## Start with default wallpaper

```bash
./aurora.py
```

---

## Use custom wallpapers

One file per monitor:

```bash
./aurora.py wallpaper1.jpg wallpaper2.mp4
```

Example:

```
Monitor 1 → wallpaper1.jpg
Monitor 2 → wallpaper2.mp4
```

If fewer files are provided than monitors, the last file is reused.

---

## Video wallpapers with audio

By default videos are muted.

Enable audio:

```bash
./aurora.py --audio video.mp4
```

---

## Wallpaper scaling modes

### Fill (default)

Crops the wallpaper to fill the screen:

```bash
./aurora.py --mode fill wallpaper.mp4
```

### Fit

Keeps aspect ratio and adds borders:

```bash
./aurora.py --mode fit wallpaper.mp4
```

### Stretch

Stretches wallpaper to screen size:

```bash
./aurora.py --mode stretch wallpaper.mp4
```

---

# Autostart

Enable Aurora Lite on login:

```bash
./aurora.py --install-autostart
```

Remove autostart:

```bash
./aurora.py --remove-autostart
```

---

# Stopping Aurora Lite

Stop the background instance:

```bash
./aurora.py --stop
```

---

# Supported Formats

## Images

| Format | Extension | Supported |
|---|---|---|
| JPEG | `.jpg` `.jpeg` | ✅ |
| PNG | `.png` | ✅ |
| WebP | `.webp` | ✅ |
| Bitmap | `.bmp` | ✅ |
| GIF | `.gif` | ✅ |
| TIFF | `.tiff` | ✅ |

---

## Videos

| Format | Extension | Supported |
|---|---|---|
| MPEG-4 Video | `.mp4` | ✅ |
| Matroska Video | `.mkv` | ✅ |
| WebM | `.webm` | ✅ |
| QuickTime Video | `.mov` | ✅ |
| AVI Video | `.avi` | ✅ |
| MPEG-4 Container | `.m4v` | ✅ |

Video support depends on installed GStreamer codecs.

For additional formats install:

```bash
sudo apt install ubuntu-restricted-extras
```

---

# Wallpaper Workspace

Aurora Lite automatically creates:

```
workspace/
├── .upload/
├── images/
├── videos/
└── .trash/
```

## Importing wallpapers

Place wallpapers into:

```
workspace/.upload/
```

Files beginning with:

```
wallpaper-
```

are automatically imported.

Example:

```
workspace/.upload/wallpaper-space.mp4
workspace/.upload/wallpaper-mountain.jpg
```

After refresh:

```
workspace/
├── images/
│   └── wallpaper-mountain.jpg
│
└── videos/
    └── wallpaper-space.mp4
```

---

# Desktop Controls

Right-click any wallpaper:

- Play / Pause video
- Mute / Unmute audio
- Change wallpaper
- Upload wallpaper folders
- Refresh imports
- Quit Aurora Lite

The menu displays the active monitor connector:

Example:

```
Display: HDMI-1
Display: DP-2
```

---

# Project Structure

```
aurora-lite/
│
├── aurora.py              # Main application
├── wallpaper-default.jpg   # Default wallpaper
├── README.md
│
├── workspace/              # Created automatically
│   ├── images/
│   ├── videos/
│   ├── .upload/
│   └── .trash/
│
└── .aurora.log             # Runtime log
```

---

## License

This project is licensed under the **MIT License**.

See the [LICENSE](LICENSE) file for the full license text.
