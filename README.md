# Cosmere-Cam / Dynamic Teleprompter

Windows 11 VTuber production application built with Python/PySide6 and optional native C++ components.

**Source migration in progress:** The current tested baseline is v0.5.0-alpha16.1. Its source must be imported before this repository can build a usable application. GitHub workflow artifacts are source ZIPs, not bundled Windows executables.

## Systems
Script playback, fixed-resolution canvas recording with FFmpeg, image/video and Live2D layers, VST3 audio filters and microphone monitoring, idle Live2D animation, saved model switching, and experimental WASD perspective movement.

## First priority
Profile and repair WASD freezing/teleporting and boundary crossing. Preserve the existing working features. Write deterministic unit tests for speed, catch-up frame times, head-turn coupling and boundary clamping before changing movement logic.

## Running from source
After importing source, run `setup_windows.bat` and `start_windows.bat` from the `dynamic_teleprompter` directory.

Live2D Cubism Core must be installed separately from the official SDK under its license. Do not commit its DLL, SDK archive, proprietary models, VST plugins, recordings, project files, or credentials.

See [AGENTS.md](AGENTS.md) for development guidance.
