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

Install the official ReaPlugs 2.36 **x64** separately, then use Microphone → Recording → Add ReaPlugs / VST2. Plugin controls opens the original editor. Ordered instances, bypass and complete plugin state (including learned ReaFIR noise curves) are saved with projects/settings. Tune before recording. This is a mono voice path; MIDI, instruments, external sidechains, surround and 32-bit plugins are outside this release. Plugin failures bypass the effect.

The separate native VST2 helper uses the independently reverse-engineered FST ABI under GPL-3.0-or-later. See dynamic_teleprompter/native/vst2 for license, provenance and complete source. No vendor plugin binaries are distributed.

## Recording processing and text layers

Start Recording waits for offline voice tracking to open the microphone, then starts capture and focuses Output for model controls. Stop ends capture and asks which optional processes to run: edge-only silence trimming, shortening internal pauses, and script-to-speech word alignment. Save without processing remains available; Discard skips every processing stage. Optional processing failures retain the original take.

Word alignment writes a `.alignment.json` sidecar and opens a full-script timing review. Unmatched words are interpolated between reliable neighbors, retained in full and flagged for approval. This estimates script timings; it does not measure audio/video offset or prove missing words were spoken. Saved reviews can be reopened from Microphone → Recording.

Text Output in the Visual Layer Editor supports transforms, opacity, color filters, font/size/color and drag positioning. Layer locks apply to text. Background Cubism pose rendering uses QImage workers; the canvas shares frames between preview/recording and caches idle text. Slow model pose updates may still have a lower rate than video layers. Automated performance checks use a synthetic model; licensed-model/hardware verification remains separate.

## Arrow controls, directional models and session resume (alpha23)

Arrow keys control the active Live2D character from any Suite window, including dialogs and menus. Editable text fields retain cursor navigation. Outside text, arrows do not navigate buttons, lists, combos or sliders, even when movement is disabled. WASD remains supported; switching to another application or a text field releases movement. Enable movement for a selected character in Canvas → Visual Layer Editor → Movement.

Movement settings offer Head turning, Directional models, or both. Assign each direction and the resting default from the local Model Library. Last pressed direction wins on diagonals; releasing it selects any remaining held direction. On release choose the default model or keep the last used model. Unassigned directions use the original layer model. Position, scale and layer appearance remain unchanged by model selection. Up/down retain depth/perspective behavior. Assigned model poses are warmed on background workers, with an eight-worker cache; first use and large models can still take time and consume more memory. Missing/unrenderable models retain the last ready pose where available. Models must be installed locally and are never included in releases.

The workspace checkpoints every five seconds and on normal close: script, layers, effects, settings, current slide, selected tabs, recording directory, microphone selection and main/Output/Reader geometry/visibility. Launch resumes the checkpoint with microphone, tracking and recording stopped. Explicit project files remain separate; crashes may lose up to the last checkpoint interval. Layer editor and transient modal dialogs are not reopened.

Voice following uses local script matching with contraction expansion, conservative unambiguous long-word corrections, and stronger evidence for larger forward jumps. Repeated phrase recovery remains forward-only; manually select an earlier slide to reread a previous section. Dynamic Vosk models receive script phrase/word hints; out-of-vocabulary fantasy names are not added to a model's acoustic dictionary. Microphone status warns about detected clipping and quiet/silent input. Microphone → Voice Tracking → Choose recognition model folder allows a separately installed larger English Vosk model; larger static models use unrestricted recognition. Hardware and actual licensed-model performance require separate user testing.
