# Windows releases and updates

Change `dynamic_teleprompter/version.py` for each release. A main push runs the Windows
tests, freezes the actual application and stable launcher, compiles the optional native
engine, creates a one-click setup EXE, exercises installation/upgrade/recovery, uploads
a draft release, checks uploaded bytes against GitHub digests and the update manifest,
publishes, then downloads and installs through the shipping updater on Windows.
Failed published verification withdraws the new release to draft. Do not reuse version
numbers or replace package/manifest assets after publishing. A PR runs the same build
and installation tests without publishing. Resolved dependency versions and automated
verification reports accompany each release. No local repackaging is required.

## Install and migrate

Download **Cosmere-Video-Recording-Suite-Setup.exe** from this public repository's Releases page. Run
as the ordinary Windows user; no Python, ZIP extraction or administrator rights needed.
The Start menu shortcut launches `%LOCALAPPDATA%\CosmereCam\CosmereCamLauncher.exe`.
Windows may show SmartScreen because the EXE has no commercial Authenticode certificate.

Old source-only alphas have no updater and cannot discover new code by themselves.
Run the new installer once, then use its Start menu shortcut. Do not delete your old
source folder if it contains local models/assets. Existing home-directory settings,
dictionary, model paths, projects and `%LOCALAPPDATA%\DynamicTeleprompter\Runtime`
are preserved in place. The updater never copies/deletes these directories. Moved model
folders may still need their paths selected again. A source checkout with this new code
can bootstrap the managed application through the Updates tab.

## Public update access

Public release checks and downloads require no GitHub account or token. Checks run
5 seconds after launch and every 6 hours, including alpha prereleases. Optional
GitHub access is stored in Windows Credential Manager; expired credentials fall
back to anonymous access. Alpha17 installations without connected access need the
new installer once because their old controller requires credentials.

User-facing branding is Cosmere Video Recording Suite. Internal CosmereCam paths,
launcher filenames and protocol-v1 package names stay stable for existing installs.

## Deployment and trust

GitHub HTTPS and write access to the fixed public repository are the update trust root.
The updater requires GitHub's server-generated SHA-256 digest for both the manifest
and payload, binds manifest version/platform/size/hash to the matching release asset,
and verifies a complete internal file inventory before extraction and deployment.
Credentials are stripped from cross-host download redirects. Arbitrary hosts, traversal,
links, duplicate/case-colliding filenames and private/licensed assets are rejected.
This is repository-authenticated distribution, not independent offline signature
verification or protection against a compromised publishing account. Keep GitHub access
secured; hardware-backed code signing can be added when a signing identity is available.

Versions live side by side under `CosmereCam\versions`. `active.json` is replaced
atomically after extraction succeeds. The stable protocol-v1 launcher is not overwritten
by routine updates. `transaction.json` preserves the prior pointer until the new Qt app
acknowledges startup. An interrupted first boot rolls back on the next launch; a failed
startup rolls back immediately. The OS-released launcher lock covers the whole app life.
If startup times out while the process is still alive, the launcher restores the old
pointer for the next launch and waits for natural exit; it never kills a possible capture.
Downloads use `.part` files, delete incomplete transfers and retry from zero. Settings
and user content are never inside version folders. Old versions are retained for recovery;
automatic pruning is deliberately deferred. Re-running setup repairs a damaged version
only after the damaged folder is renamed by the user; no updater deletes a running app.

Updates download without blocking Qt or capture. Restart & Update refuses during recording,
monitoring, voice following, model download or speech tests. Normal close retains project
save prompts and recording-finalization guards, shuts down audio/tracking, then schedules
installation after process exit. A ready update also installs on a normal close.

## Verification boundaries

Unit tests cover networking failure, interrupted/corrupt downloads, authentication/digests,
malicious packages, atomic deployment faults, restart recovery and critical-operation gates.
Windows integration executes the actual frozen installer/launcher/Qt application and FFmpeg
capture/discard, verifies legacy-data persistence, failed EXE boot rollback and the published
release download. The previous-version protocol fixture is explicitly synthetic because the
repository had no historical Windows releases. Existing alpha users need the one-time installer.
Official x64 ReaEQ/ReaComp/ReaGate/ReaFIR processing, native editor opening, saved
state, synthetic learned-profile restoration and recording delay compensation are
verified separately by tools/verify_reaplugs.py. Vendor DLLs stay in ignored build
folders and are never packaged. Real microphone input, listening quality, cameras,
Cubism Core and licensed models require a manual
smoke test on the user's workstation. Automated reports must not be described as those tests.

Bundled open-source runtime/dependency notices are under `licenses/` in each payload.
FFmpeg is supplied by imageio-ffmpeg; retain its upstream license and corresponding-source
links when redistributing: https://github.com/imageio/imageio-ffmpeg and https://ffmpeg.org/.
