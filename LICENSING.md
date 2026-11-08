# Licensing boundaries for v0.6.0

The LICENSE file adapts the Archives of Arcanum Community License v0.1 to the original Cosmere Video Recording Suite code and documentation for future releases. It does not retroactively change prior releases or claim rights over user output, media, models, plugins, or other rights holders' material. These are custom, unreviewed terms; the project is not described as OSI-approved open source.

## Separately licensed components

- `dynamic_teleprompter/native/vst2/` and Cosmere's independent native VST2 host retain GPL-3.0-or-later, unchanged FST provenance and corresponding-source/build material.
- `dynamic_teleprompter/vst3_worker.py` and CosmereVST3Host are GPL-3.0-or-later, with COPYING.GPL3 and VST3-HOST.md. Pedalboard is imported only by that independent helper. The GUI/client communicate through a documented, bounded JSON/PCM process protocol and do not import the Pedalboard module.
- FFmpeg remains a separate executable with its own build-dependent LGPL/GPL notices and source obligations. PySide6/Qt retain their LGPL/GPL/commercial alternatives; the distributed runtime's applicable notices remain intact. The installer uses an ordinary directory payload so libraries can be replaced; no restriction here overrides component modification, reverse-engineering or replacement rights required by those licenses.
- NumPy, Vosk, PortAudio/sounddevice, pyspellchecker, keyring, OpenCV, PyInstaller and other bundled components retain their own licenses. Build tooling collects distribution metadata and license notices into the payload's `licenses` directory. Those notices do not substitute for any required corresponding source or source offer; releases must preserve applicable source/build links and notices for their exact dependency versions.
- Cubism Core/SDK, proprietary Live2D assets and vendor VST binaries are not distributed with the Suite. User-requested project exports may contain the user's explicitly referenced model/media data; the user must hold the rights to share those assets. Runtime libraries and plugin binaries are not included in project exports.

The Suite's noncommercial redistribution/custom commercial licensing terms do not restrict the separate GPL helpers or other separately licensed components. Their own permissions, including permitted commercial redistribution, remain unchanged. Original Suite contributors retain copyrights; no blanket right to relicense third-party contributions is asserted. This document describes the technical separation and known notices, not a completed legal provenance audit or legal opinion. Commercial licensing may grant only rights held by the maintainer.

## Source and build

The source ZIP for each release includes the exact tracked Suite source, GPL helper source, FST header/provenance, CMake and Windows build scripts. `requirements-resolved.txt` records the build's Python package versions. Third-party dependency source/build information is recorded in `licenses/DEPENDENCY-SOURCES.md`; it must remain available alongside redistribution. The helpers are independently runnable as described in their README/protocol documents.
