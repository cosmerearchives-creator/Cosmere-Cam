# Cosmere Video Recording Suite

Windows 11 VTuber production application built with Python/PySide6 and optional native C++ components.

Windows releases include a self-contained per-user installer and automatic background updates.
Download **Cosmere-Video-Recording-Suite-Setup.exe** from Releases, run it, then use the Start menu shortcut.
No Python installation or administrator rights are required. See [RELEASING.md](RELEASING.md)
for public automatic updates, migration from source-only alphas, deployment recovery and test limits.

## Systems
Script playback, fixed-resolution canvas recording with FFmpeg, image/video and Live2D layers, ReaPlugs/VST2 and VST3 audio filters and microphone monitoring, idle Live2D animation, saved model switching, and experimental WASD perspective movement.

## First priority
Profile and repair WASD freezing/teleporting and boundary crossing. Preserve the existing working features. Write deterministic unit tests for speed, catch-up frame times, head-turn coupling and boundary clamping before changing movement logic.

## Running from source
After importing source, run `setup_windows.bat` and `start_windows.bat` from the `dynamic_teleprompter` directory.

Live2D Cubism Core must be installed separately from the official SDK under its license. Do not commit its DLL, SDK archive, proprietary models, VST plugins, recordings, project files, or credentials.

See [AGENTS.md](AGENTS.md) for development guidance.


## ReaPlugs voice effects

Install the official ReaPlugs 2.36 **x64** separately, then use Output → Recording → Add ReaPlugs / VST2. Plugin controls opens the original editor. Ordered instances, bypass and complete plugin state (including learned ReaFIR noise curves) are saved with projects/settings. Tune before recording. This is a mono voice path; MIDI, instruments, external sidechains, surround and 32-bit plugins are outside this release. Plugin failures bypass the effect.

The separate native VST2 helper uses the independently reverse-engineered FST ABI under GPL-3.0-or-later. See dynamic_teleprompter/native/vst2 for license, provenance and complete source. No vendor plugin binaries are distributed.
