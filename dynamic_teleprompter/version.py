"""Single release version; changing main triggers a tested release."""
APP_NAME = 'Cosmere Video Recording Suite'
VERSION = '0.6.1'
RELEASE_NOTES = 'Fix processing stalls caused by incompatible external FFmpeg builds: trimming, timing-review previews and exports prefer the included release-tested encoder.\nCustom encoder locations remain available for recording and video playback. Diagnostics record the capture/processing encoder choice.\nTrimmed video uses a bounded encoder thread count and standard H.264 color format.'
