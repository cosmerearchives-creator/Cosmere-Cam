# Coding agent instructions

This repository contains a Windows Python/PySide6 app and native C++ components. Begin by reading README.md and inspecting the current source. **Do not rewrite from scratch.**

## Ground rules
- Keep a working launcher (`dynamic_teleprompter/start_windows.bat`) and preserve voice following, recording, visual layers, audio filters, Live2D model rendering and camera-free idle animations.
- Proprietary Live2D Cubism SDK/Core binaries, licensed models and private user assets must remain outside source control. Runtime discovery uses a user-level installation directory.
- Prefer targeted changes and tests. Do not assert Windows runtime tests passed unless actually run.
- Use Python 3.13 as the CI syntax-check baseline; note that Python compatibility with additional versions must be verified, not assumed.
- Keep input/control updates independent of heavy Cubism pose computation. Avoid long operations on the Qt UI thread.
- Audio callbacks must not block on Python UI code. Third-party VST hosts are experimental and must fail safely.

## First issue: alpha16.1 WASD jitter
Reproduce the movement bug with deterministic time-stepped tests. Profile Qt event handling, Live2D render cost, input state, frame delta integration and movement bounds. Clamp *proposed* positions to geometry-aware bounds after movement; don't merely test before updating. Never accumulate huge catch-up distances after stalls. Verify the character remains at its new position on key release while its head smoothly returns to center. Track movement speed using a lightweight virtual head-angle signal; rasterization should not control movement speed.

## Deliverables
Short change summary, relevant tests and results, risks, and where the Windows test artifact is available. Prefer a PR for substantive changes.

## Standing release policy
Completed and adequately tested updates must be merged into main and published by default.
Use Implement → Test → Merge to main → Build → Publish release → Verify update detection.
A PR alone is not completion. Do not leave completed work in an open PR unless the user
explicitly asks to hold it. If the user says not to push an update to main, first ask
them to confirm that they intend to hold it back. Never bypass failing tests, release
verification, security checks or unresolved critical defects to satisfy this policy.
Report blockers and fix them wherever feasible. Bump dynamic_teleprompter/version.py for
each release; main pushes run the Windows build/release workflow automatically.
Keep updater protocol v1 compatible with installed stable launchers. Do not overwrite
the stable launcher during routine updates or remove the last known-good version.
All private/recorded/licensed data stays outside distributables. Report automated and
manual hardware/model tests separately; never claim end-to-end updates without evidence.


