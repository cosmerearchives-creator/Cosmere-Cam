"""Manual cut decisions and caption preparation for a saved recording."""
import math
import tempfile
from pathlib import Path

from edit_pipeline import complement, cut_media, propose_retakes, run_media
from recording_processing import align_script, recognize_words


def frame_time(value, data):
    return min(data['duration'], max(0., round(value * data['source']['fps']) / data['source']['fps']))


def kept_ranges(data, cuts):
    snapped = [dict(start=frame_time(c['start'], data), end=frame_time(c['end'], data), approved=True)
               for c in cuts]
    if not any(c['end'] > c['start'] for c in snapped):
        return [(0., data['duration'])]
    return complement(data['duration'], snapped)


def caption_groups(data):
    """Keep the recorded grouping only if it covers the entire written script."""
    groups = [group for group in data.get('render_project', {}).get('groups', []) if group.split()]
    script = data['script'].split()
    if [word for group in groups for word in group.split()] == script:
        return groups
    return [' '.join(script[i:i + 8]) for i in range(0, len(script), 8)]


def full_script_events(data, groups):
    """Give every group screen time even when recognition has no timing anchor."""
    duration = data['duration']
    if not groups:
        return [(0., '', 0)]
    # The overlay runs at the capture frame rate. Fit every group into at
    # least one actual output frame, including the final estimated group.
    fps = data.get('source', {}).get('fps', 30)
    frames = max(1, round(duration * fps))
    if len(groups) > frames:
        raise ValueError('Keep more footage so the full script can be displayed.')
    dwell_frames = max(1, min(round(.45 * fps), frames // len(groups)))
    cursor = 0
    events = []
    for number, group in enumerate(groups):
        anchor = data['words'][cursor]['start']
        earliest = round(events[-1][0] * fps) + dwell_frames if events else 0
        latest = frames - dwell_frames * (len(groups) - number)
        events.append((min(latest, max(earliest, round(anchor * fps))) / fps, group, number))
        cursor += len(group.split())
    if events[0][0] > 0:
        events.insert(0, (0., '', 0))
    return events


def detect_cough_candidates(samples, rate, recognized):
    """Flag isolated bursts outside recognized speech. These are guesses."""
    import numpy as np
    width = rate // 50
    count = len(samples) // width
    if count < 20:
        return []
    rms = np.empty(count, dtype=np.float32)
    for first in range(0, count, 3000):
        last = min(count, first + 3000)
        frames = np.asarray(samples[first * width:last * width], dtype=np.float32).reshape(-1, width)
        rms[first:last] = np.sqrt(np.mean(frames * frames, axis=1))
    floor = max(350., float(np.percentile(rms, 25)) * 6)
    active = rms > floor
    changes = np.flatnonzero(np.diff(np.r_[False, active, False]))
    result = []
    for first, last in zip(changes[::2], changes[1::2]):
        if not 5 <= last - first <= 35:
            continue
        a, b = first * .02, last * .02
        if any(word['start'] < b + .08 and word['end'] > a - .08 for word in recognized):
            continue
        if active[max(0, first - 6):first].any() or active[last:last + 6].any():
            continue
        burst = samples[first * width:last * width]
        crossings = np.mean((burst[:-1] >= 0) != (burst[1:] >= 0))
        if crossings < .12 or float(rms[first:last].max()) < floor * 2:
            continue
        result.append(dict(start=max(0., a - .05), end=min(len(samples) / rate, b + .07),
                           reason='Possible cough or mouth noise'))
    return result


def suggest(data, ffmpeg, work, cancel):
    if not data['source']['audio']:
        return []
    import numpy as np
    source = data['source']['original']
    model = data.get('model_path')
    recognized = []
    if model and (Path(model) / 'am/final.mdl').is_file():
        recognized = recognize_words(source, ffmpeg, model, work, cancel=cancel)
    wav = Path(work) / 'suggestions.pcm'
    run_media([ffmpeg, '-y', '-v', 'error', '-i', source, '-vn', '-ac', '1',
               '-ar', '16000', '-f', 's16le', str(wav)], cancel)
    samples = np.memmap(wav, dtype='<i2', mode='r') if wav.stat().st_size else np.array([])
    try:
        coughs = detect_cough_candidates(samples, 16000, recognized)
    finally:
        del samples
    return sorted(coughs + propose_retakes(data['script'], recognized), key=lambda c: c['start'])


def prepare_captions(data, cuts, ffmpeg, work, cancel, progress):
    from pronunciations import rewrite_words
    source = dict(data['source'])
    kept = kept_ranges(data, cuts)
    duration = sum(b - a for a, b in kept)
    if cuts:
        progress('Applying your cuts to the picture and audio')
        scene = Path(work) / 'scene.mp4'
        cut_media(source['scene'], scene, kept, ffmpeg, work, audio=source['audio'], cancel=cancel)
        source['scene'] = str(scene)
        if source.get('foreground'):
            foreground = Path(work) / 'foreground.mov'
            cut_media(source['foreground'], foreground, kept, ffmpeg, work, audio=False,
                      alpha=True, premultiplied=source['foreground_premultiplied'], cancel=cancel)
            source['foreground'] = str(foreground)
    saved = data.get('caption_review', {})
    if saved.get('kept') == [list(span) for span in kept]:
        recognized = None
    else:
        recognized = []
    model = data.get('model_path')
    if recognized is not None and source['audio'] and model and (Path(model) / 'am/final.mdl').is_file():
        progress('Finding caption timings in the kept speech')
        recognized = recognize_words(source['scene'], ffmpeg, model, work, cancel=cancel)
        recognized = rewrite_words(recognized, data.get('pronunciations', []), data['script'])
    if cancel.is_set():
        raise RuntimeError('Preparation cancelled.')
    aligned = (dict(schema=1, script=data['script'], duration=duration, words=saved['words'])
               if recognized is None else align_script(data['script'], recognized, duration))
    aligned.update(source=source, render_project={**data['render_project'], 'groups': caption_groups(data)},
                   kind='manual_cut', output_stem=data['output_stem'],
                   processing_directory=data['processing_directory'])
    full_script_events(aligned, caption_groups(data))
    return aligned
