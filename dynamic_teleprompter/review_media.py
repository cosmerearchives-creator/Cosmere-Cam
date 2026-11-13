"""Temporary review media. The original remains the source for edits and exports."""
from pathlib import Path

from edit_pipeline import run_media


WAVEFORM_RATE = 50


def audio_peaks(path):
    import numpy as np
    # Decode mono PCM at 8 kHz, then retain one absolute peak per 20 ms.
    width = 8000 // WAVEFORM_RATE
    peaks = []
    with Path(path).open('rb') as stream:
        while block := stream.read(width * 2 * 3000):
            samples = np.frombuffer(block, dtype='<i2').astype(np.int32)
            tail = len(samples) % width
            complete = samples[:len(samples) - tail] if tail else samples
            peaks.extend((np.abs(complete.reshape(-1, width)).max(axis=1) / 32768).tolist())
            if tail:
                peaks.append(float(np.abs(samples[-tail:]).max()) / 32768)
    return peaks


def build_waveform(ffmpeg, source, directory, cancel):
    pcm = Path(directory) / 'waveform.pcm'
    try:
        run_media([ffmpeg, '-nostdin', '-y', '-v', 'error', '-i', source,
                   '-map', '0:a:0', '-ac', '1', '-ar', '8000', '-f', 's16le', str(pcm)], cancel)
        return audio_peaks(pcm)
    finally:
        pcm.unlink(missing_ok=True)


def build_preview(ffmpeg, source, directory, cancel):
    target = Path(directory) / 'preview.mp4'
    # One keyframe per frame makes random seeks independent of GOP length.
    run_media([ffmpeg, '-nostdin', '-y', '-v', 'error', '-threads', '2', '-i', source,
               '-map', '0:v:0', '-map', '0:a:0?', '-vf',
               'scale=w=min(720\\,iw):h=min(720\\,ih):force_original_aspect_ratio=decrease:force_divisible_by=2',
               '-filter_threads', '1', '-c:v', 'libx264', '-preset', 'ultrafast',
               '-crf', '26', '-g', '1', '-threads:v', '2', '-pix_fmt', 'yuv420p',
               '-c:a', 'copy', '-movflags', '+faststart', str(target)], cancel)
    return target
