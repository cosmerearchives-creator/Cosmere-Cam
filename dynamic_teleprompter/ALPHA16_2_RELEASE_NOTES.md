# v0.5.0-alpha16.2

- Reader text defaults to 36 pt, with a saved 20–96 pt size control in the Reader window.
- Speech progress advances the scroll target within visual lines. Time-based easing keeps fractional position and recalculates after reflow/resize.
- Recording tab → Discard Recording asks for confirmation (Cancel is the default and Escape choice). Confirming stops capture and asynchronously terminates encoding, joins workers, closes audio and removes the take's temporary directory. It never enters Stop/Save finalization, muxing, diagnostics or export. Existing destination files remain untouched.
- Stop Canvas Recording retains the normal save flow. Discard is unavailable once saving begins. Cleanup errors are reported rather than claiming success.

This is a source package, not an executable installer. Run setup_windows.bat, then start_windows.bat. Install licensed Live2D Core and models separately.

Validation: automated Qt offscreen and recorder lifecycle tests; see the PR for exact environments/results. Real microphone, VST and Live2D interaction still require a manual Windows smoke test.

Manual check: follow speech over several wrapped lines, pause and correct backward, resize and change font size. Record a short take, decline discard and confirm it continues; confirm discard and verify no MP4/diagnostics appear; record again and Stop to verify a playable saved MP4.

