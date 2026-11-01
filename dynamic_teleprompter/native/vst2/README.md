# Cosmere VST2 host

This **separate process** is GPL-3.0-or-later. See COPYING and the SPDX notice in
host.cpp. It uses FST's independently reverse-engineered interface, not Steinberg's
unavailable proprietary VST2 SDK. The FST header and license are reproduced unchanged
from https://github.com/pierreguillot/FTS commit
`b16f9b7ce8ab9675747e413f2d1b51df8df142df`; upstream is
https://git.iem.at/zmoelnig/FST/. Copyright notices are retained.

Build with the parent CMake project on Windows x64. The release source archive contains
this host's complete corresponding source, FST interface, license and CMake build rules.
The application communicates with the independently executable helper over a bounded
binary stdin/stdout protocol. Plugin DLLs are installed by the user and never shipped.

Protocol: little-endian uint32 operation and payload size, then payload. Reply has
uint32 status (0 success), size, then payload. Maximum payload 16 MiB. Operations:
0 metadata JSON; 1 mono float32 processing (up to 2048 frames at 48 kHz);
3 show native editor; 4 parameter index/float value; 5 capture opaque state;
6 restore state; 7 generic parameters JSON; 8 clean shutdown.

The voice path is mono. Stereo main inputs duplicate it, additional inputs are silent,
and the first two outputs downmix to mono. Instruments, MIDI routing, external
sidechains, surround output, shell plugins and 32-bit DLLs are outside this voice-host
release. Native plugin editors retain the vendor's appearance.
