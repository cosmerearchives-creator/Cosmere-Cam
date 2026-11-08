# Independent VST3 host

`vst3_worker.py` and its compiled CosmereVST3Host executable are GPL-3.0-or-later. See COPYING.GPL3. They load Pedalboard under its GPLv3 license and do not import the Suite GUI. The app communicates using the documented length-prefixed JSON/PCM protocol in the source. The worker can be run independently from a Python installation with NumPy and Pedalboard, or as the separate Windows executable. The complete worker source and build instructions accompany each release source archive.

This helper and its GPL dependencies are excluded from the Suite Community License. Their GPL permissions, including commercial redistribution rights, remain intact. Plugin binaries are installed separately by the user. This is a technical separation and notice, not a completed legal opinion on every dependency.
