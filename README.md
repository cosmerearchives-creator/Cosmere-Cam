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

The workspace checkpoints every five seconds and on normal close: script, layers, effects, settings, current slide, selected tabs, recording directory, microphone selection and main/Output/Reader geometry/visibility. Launch resumes the checkpoint with voice following, camera tracking and recording stopped. Existing idle-animation microphone lip sync remains controlled by its saved setting. Explicit project files remain separate; crashes may lose up to the last checkpoint interval. Layer editor and transient modal dialogs are not reopened.

Voice following uses local script matching with contraction expansion, conservative unambiguous long-word corrections, and stronger evidence for larger forward jumps. Repeated phrase recovery remains forward-only; manually select an earlier slide to reread a previous section. Dynamic Vosk models receive script phrase/word hints; out-of-vocabulary fantasy names are not added to a model's acoustic dictionary. Microphone status warns about detected clipping and quiet/silent input. Microphone → Voice Tracking → Choose recognition model folder allows a separately installed larger English Vosk model; larger static models use unrestricted recognition. Hardware and actual licensed-model performance require separate user testing.

## Approval, corrected exports and retake editing (alpha24)

Start Recording switches speech following to Automatic, unmutes an existing speech worker, and preserves that worker while resetting the first slide. No additional startup toggle is required. The first-slide control now has room for its full label.

New recordings enable **Prepare editable source** under Microphone → Recording by default. This records synchronized background and alpha foreground tracks in addition to the original take, so text can be replaced while foreground media remains above it. This uses additional disk space and encoding work; the option can be disabled for ordinary capture. Pixel conversion runs on encoder threads, and preview/editor views share the capture's canvas clock. If an auxiliary track fails, the playable original capture is retained and corrected export is unavailable for that take.

On Stop choose basic trimming, word alignment, and optionally assisted or automatic advanced retake editing. The internal-pause threshold is configurable. Analysis produces an `.alignment.json` review and a `.take` source folder; with editable alignment selected, no finished video is exported until approval. **Save review** saves progress with confirmation. **Approve & Export** requires all estimated/adjusted word timings to be approved, renders the full script's approved caption sequence, and creates `…_baseline.mp4`. If advanced editing was selected it also creates `…_edited.mp4`, plus an edit map and remapped timing report. Both versions include the same selected basic trimming and corrected text; only the advanced version applies approved retake cuts. The original source bundle is never overwritten. Save without processing preserves normal captured-video behavior; Discard still deletes the take without analysis/export.

Corrected export retains recorded media/movement, text font, static appearance, layer transforms, grouping and stacking, and alpha foreground order. It replaces live speech/compare text with approved script captions and does not replay real-time entrance/exit animations. Edits and re-exports use the original take's review; the edited timing sidecar is a report, not a second editable source. Keep the `.take` folder alongside its review. Older recordings without these source tracks can save reviews but cannot cleanly replace baked-in text.

Assisted mode proposes local, script-anchored repeated or abandoned phrases and leaves removal unchecked for review. Preview selected cut/join plays surrounding scene/audio with the proposed section removed; corrected captions appear in the final export. Automatic mode prechecks only conservative, confident repeated attempts; uncertain proposals remain uncut unless approved. Intentional repetition written into the script is excluded. Short stumbles and recognition mistakes can be missed, and automatic decisions should be checked on real recordings. Cough/throat-clear/filler detection and the synchronized side-by-side comparison video remain future phases. No speech-generation or missing-word audio synthesis is performed.

Source media stays in the local recording folder. Export runs in the background, supports cancellation, publishes through temporary files, and blocks update/restart while active. The edit map records reasons and applied frame-aligned cuts for future comparison tools. Session restoration from alpha23 remains unchanged; it can restore only checkpoints actually saved by a version supporting it.

## Retake recovery and capture work (alpha25)

Retake proposals use unique script anchors and tolerate errors surrounding those anchors, Unicode apostrophes, short restart gaps, and longer attempts (up to 160 words / 90 seconds between matching anchors). Consecutive failed attempts can form one reviewable cut; smaller stutters inside those attempts do not hide the larger restart. Intentional repetition written into the script is excluded where anchors are ambiguous. Only strong exact repeats with confident recognition and an audible gap qualify for automatic approval; other proposals remain unchecked. Different recordings can still require manual review, especially when recognition loses the anchor itself.

Speech recognition and restart detection now run on the original audio before basic pause trimming. Recognition times and proposed cuts are then mapped through the selected trim segments to the baseline timeline. Original recognition times and pre-trim cut boundaries are retained in the review for diagnosis. Word alignment uses the same apostrophe normalization while retaining the complete original script for display.

Editable capture writes one lossless layered stream instead of encoding three independent videos during capture. After Stop, source-track preparation reconstructs the original capture, background and alpha foreground before alignment/review/export. This trades additional temporary disk use and preparation time after Stop for less live encoding work. Explicit Discard kills capture without unpacking or running analysis. If preparation fails, the layered capture is retained as a `.recovery.mkv` with layout metadata rather than being presented as a finished video.

Fade/glow passes are cached separately to preserve their opacity compositing; scale/rotation animations continue using vector paths. Transparent model margins are excluded from painting without changing the model's coordinate system. Expensive poses receive a bounded adaptive update interval; movement interpolation and canvas position remain independent. Repeated uses of the same media can share scaled frames. New diagnostics include `fresh_capture_fps`, `duplicate_frame_percent`, `canvas_stage_mean_ms`, and capture-mode information. File FPS alone is not evidence that every frame is fresh.

Regression fixtures use generic scripts and synthetic media. User recordings, transcripts, licensed models and Core binaries remain outside the repository and release artifacts. Actual microphone/recognition and licensed-model performance require real-use verification; the next recording's diagnostics are the comparison point.

## Compact editable capture and stall recovery (alpha26)

Editable capture compresses video color with H.264 YUV420 CRF 18 and stores an independent lossless grayscale H.264 transparency mask. It preserves full resolution and 30/selected FPS, but color is no longer mathematically lossless. The original, background and foreground sources are reconstructed after Stop; ordinary recording and approval/export behavior remain intact. Disk-rate regression checks accompany the portrait capture test.

VST2 requests bound lock acquisition and pipe writes as well as reply waits. A timed-out host is killed and its effect bypassed. Recording errors finalize the video stream independently of audio-worker completion. Recovery retains processed WAV audio when safely closed; if a worker is still writing, its temporary workspace is retained instead of deleted. Explicit Discard retains its deletion semantics. Diagnostics record capture bytes and bounded thread-stack snapshots after encoder/audio stalls. Snapshots contain local file paths and should be shared privately.
