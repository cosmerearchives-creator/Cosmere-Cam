"""Optional recording processing. Never modifies the original input in place."""
import json
import re
import subprocess
from difflib import SequenceMatcher
from pathlib import Path


def align_script(script, recognized, duration):
    tokens = re.findall(r"\S+", script)
    from edit_pipeline import normalize_word as normalized
    words = [dict(word=word, start=None, end=None, status='Estimated timingâ€”needs approval',
                  needs_approval=True) for word in tokens]
    heard = [item for item in recognized if normalized(item.get('word', ''))]
    matcher = SequenceMatcher(None, [normalized(w) for w in tokens],
                              [normalized(w['word']) for w in heard], autojunk=False)
    for match in matcher.get_matching_blocks():
        for offset in range(match.size):
            item = heard[match.b + offset]
            if float(item.get('conf', 1)) < .65:
                continue
            start, end = max(0., float(item['start'])), min(duration, float(item['end']))
            if end < start:
                continue
            estimate=bool(item.get('pronunciation_estimate',False))
            status='Saved pronunciation timing estimate' if estimate else ('Matched saved pronunciation' if item.get('pronunciation_alias') else 'Matched speech')
            words[match.a + offset].update(start=start,end=end,status=status,needs_approval=estimate)
    cursor = 0
    previous_end = 0.
    while cursor < len(words):
        if words[cursor]['start'] is not None:
            words[cursor]['start'] = max(previous_end, words[cursor]['start'])
            words[cursor]['end'] = max(words[cursor]['start'], words[cursor]['end'])
            previous_end = words[cursor]['end']
            cursor += 1
            continue
        last = cursor
        while last < len(words) and words[last]['start'] is None:
            last += 1
        right = max(previous_end, words[last]['start'] if last < len(words) else duration)
        weights = [max(1, len(normalized(w['word']))) for w in words[cursor:last]]
        total = sum(weights)
        position = previous_end
        for item, weight in zip(words[cursor:last], weights):
            end = position + (right - previous_end) * weight / total
            item.update(start=position, end=end)
            position = end
        previous_end = right
        cursor = last
    return dict(schema=1, script=script, duration=duration, words=words)


def kept_segments(duration, silences, internal=False, padding=.15, minimum_pause=.6):
    """Remove edge silence; optionally shorten internal pauses, preserving padding."""
    if any(a <= .05 and b >= duration - .05 for a, b in silences):
        return [(0., duration)]
    removed = []
    for start, end in silences:
        start, end = max(0., start), min(duration, end)
        if end <= start:
            continue
        if start <= .05:
            removed.append((0., max(0., end - padding)))
        elif end >= duration - .05:
            removed.append((min(duration, start + padding), duration))
        elif internal and end - start > minimum_pause:
            removed.append((start + padding, end - padding))
    cursor, kept = 0., []
    for start, end in sorted(removed):
        if start > cursor:
            kept.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        kept.append((cursor, duration))
    # Never silently discard a fully silent recording.
    return kept if sum(b-a for a,b in kept) > .1 else [(0., duration)]


def run(command, **kwargs):
    from processing_session import current
    session=current()
    if session is not None:return session.run(command,duration=getattr(session,'duration',None),**kwargs)
    try:
        return subprocess.run(command, check=True, capture_output=True, timeout=600,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), **kwargs)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode('utf-8', 'replace') if exc.stderr else str(exc)
        if '-/filter_complex' in command and 'Unrecognized option' in detail:
            # Older user-supplied FFmpeg versions predate file-valued options.
            legacy = list(command)
            legacy[legacy.index('-/filter_complex')] = '-filter_complex_script'
            return run(legacy, **kwargs)
        raise RuntimeError('Recording processing failed: ' + detail[-2000:]) from exc


def recognize_words(source,ffmpeg,model_path,directory):
    from vosk import Model, KaldiRecognizer, SetLogLevel
    import wave
    from processing_session import current
    if current():current().begin('Extracting audio for speech alignment')
    audio = Path(directory) / 'alignment.wav'
    run([ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-i', str(source),
         '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(audio)])
    if current():current().begin('Matching recorded speech')
    SetLogLevel(-1)
    recognizer = KaldiRecognizer(Model(str(model_path)), 16000)
    recognizer.SetWords(True)
    recognized = []
    with wave.open(str(audio), 'rb') as stream:
        while True:
            from processing_session import current
            if current():current().update(min(99,100*stream.tell()/max(1,stream.getnframes())))
            block = stream.readframes(8000)
            if not block:
                break
            if recognizer.AcceptWaveform(block):
                recognized.extend(json.loads(recognizer.Result()).get('result', []))
    recognized.extend(json.loads(recognizer.FinalResult()).get('result', []))
    return recognized


def process_recording(source, ffmpeg, options, script, model_path, duration, directory, progress):
    from processing_session import current
    session=current()
    notify=progress
    def progress(stage):
        if session:session.begin(stage);session.duration=duration
        notify(stage)
    result = dict(options=options, trim_segments=[(0., duration)])
    current = Path(source)
    original_duration=duration
    original_recognized=[];original_cuts=[]
    if options.get('align'):
        progress('Analyzing speech and restarts before trimmingâ€¦')
        original_recognized=recognize_words(current,ffmpeg,model_path,directory)
        raw_recognized=list(original_recognized)
        from pronunciations import rewrite_words
        original_recognized=rewrite_words(original_recognized,options.get('pronunciations',[]),script)
        if options.get('advanced','off')!='off':
            progress('Searching for retakes…')
            from edit_pipeline import propose_retakes
            original_cuts=propose_retakes(script,original_recognized)
    trim = options.get('trim', 'none')
    if trim != 'none':
        progress('Detecting silenceâ€¦')
        detected = run([ffmpeg, '-hide_banner', '-i', str(current), '-af',
            'silencedetect=noise=-40dB:d=0.35', '-vn', '-f', 'null', '-']).stderr.decode('utf-8', 'replace')
        silences, start = [], None
        for kind, value in re.findall(r'silence_(start|end):\s*([0-9.]+)', detected):
            if kind == 'start':
                start = float(value)
            elif start is not None:
                silences.append((start, float(value)))
                start = None
        if start is not None:
            silences.append((start, duration))
        segments = kept_segments(duration, silences, trim == 'pauses', minimum_pause=float(options.get('minimum_pause', .6)))
        fps=float(options.get('source_fps',0))
        if fps>0:
            segments=[(max(0.,round(a*fps)/fps),min(duration,round(b*fps)/fps)) for a,b in segments]
            segments=[(a,b) for a,b in segments if b>a]
        result['trim_segments'] = segments
        if session:
            session.report['trim_plan']=dict(kept_segments=segments,original_duration=original_duration,expected_output_duration=sum(b-a for a,b in segments))
            session.save()
        if segments != [(0., duration)]:
            if len(segments) > 100:
                raise RuntimeError('Too many pauses to trim safely; original recording retained')
            progress('Trimming recordingâ€¦')
            from edit_pipeline import keep_filter
            graph = Path(directory) / 'trim.ffscript'
            graph.write_text(keep_filter(segments,audio=True),encoding='utf-8')
            if session:session.duration=sum(b-a for a,b in segments)
            target = Path(directory) / 'trimmed.mp4'
            run([ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-i', str(current),
                '-/filter_complex', str(graph), '-map', '[v]', '-map', '[a]',
                '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p', '-threads:v', '4', '-c:a', 'aac',
                '-movflags', '+faststart', str(target)])
            current = target
            duration = sum(b-a for a,b in segments)
    if options.get('align'):
        progress('Aligning the full script…')
        from edit_pipeline import map_time
        segments=result['trim_segments']
        recognized=[]
        for item in original_recognized:
            word=dict(item,start=map_time(float(item['start']),segments),end=map_time(float(item['end']),segments))
            if word['end']>word['start']:recognized.append(word)
        cuts=[]
        for item in original_cuts:
            cut=dict(item,start=map_time(item['start'],segments),end=map_time(item['end'],segments),
                     original_start=item['start'],original_end=item['end'])
            if cut['end']>cut['start']:
                cut['approved']=options.get('advanced')=='automatic' and cut['confidence']>=.95
                cuts.append(cut)
        preferred=[word for word in recognized if not any(cut['start']<=float(word['start'])<cut['end'] for cut in cuts)]
        result['alignment']=align_script(script,preferred,duration)
        result['alignment'].update(recognized=recognized,recognized_original=original_recognized,
            recognition_raw=raw_recognized,advanced_mode=options.get('advanced','off'),cuts=cuts,retake_detection_timeline='before_basic_trimming',
            original_duration=original_duration)
        result['retake_proposals']=len(cuts)
    return current,result
