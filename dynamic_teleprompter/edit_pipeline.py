"""Reversible edit decisions and synchronized exports. No source is modified in place."""
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path


def validate_alignment(data, approved=False):
    duration=float(data['duration'])
    if not math.isfinite(duration) or duration<=0:
        raise ValueError('Recording duration must be positive.')
    previous=0.
    for number,word in enumerate(data['words'],1):
        start,end=float(word['start']),float(word['end'])
        if not all(map(math.isfinite,(start,end))) or not previous<=start<=end<=duration+.002:
            raise ValueError(f'Check timing on word {number}; times must stay in order.')
        if approved and word.get('needs_approval',True):
            raise ValueError(f'Word {number} still needs approval.')
        previous=end


def complement(duration,cuts):
    spans=sorted((max(0.,float(c['start'])),min(duration,float(c['end']))) for c in cuts if c.get('approved'))
    cursor=0.;kept=[]
    for start,end in spans:
        if end<=start:continue
        if start>cursor:kept.append((cursor,start))
        cursor=max(cursor,end)
    if cursor<duration:kept.append((cursor,duration))
    if sum(b-a for a,b in kept)<.1:raise ValueError('Cuts would remove the entire recording.')
    return kept


def map_time(value,kept):
    elapsed=0.
    for start,end in kept:
        if value<=start:return elapsed
        if value<end:return elapsed+value-start
        elapsed+=end-start
    return elapsed


def remap_alignment(data,kept):
    import copy
    result=copy.deepcopy(data)
    result['duration']=sum(b-a for a,b in kept)
    for word in result['words']:
        word['start']=map_time(word['start'],kept)
        word['end']=map_time(word['end'],kept)
    return result


def propose_retakes(script,recognized):
    """Suggest only local script-anchored repeats. Uncertain cuts require approval."""
    normalize=lambda text:re.sub(r"[^\w']",'',text.casefold())
    target=[normalize(w) for w in script.split()]
    heard=[normalize(w.get('word','')) for w in recognized]
    cuts=[];covered=-1
    for i in range(len(heard)):
        if i<=covered:continue
        for j in range(i+2,min(len(heard),i+20)):
            if float(recognized[j]['start'])-float(recognized[i]['start'])>25:break
            n=0
            while i+n<j and j+n<len(heard) and heard[i+n]==heard[j+n] and heard[i+n]:n+=1
            if n<2:continue
            phrase=heard[j:j+n]
            anchors=[k for k in range(len(target)-n+1) if target[k:k+n]==phrase]
            if len(anchors)!=1:continue
            anchor=anchors[0];complete=0
            while j+complete<len(heard) and anchor+complete<len(target) and heard[j+complete]==target[anchor+complete]:complete+=1
            if complete<max(3,n):continue
            # Abandoned prefix or repeated full sentence; skip intentional repetition in the script.
            first_end=i+n
            if first_end<j and j-first_end>3:continue
            gap=float(recognized[j]['start'])-float(recognized[j-1]['end'])
            if gap<.25:continue
            confident=n>=5 and complete>=n and gap>=.4 and all(float(w.get('conf',1))>=.85 for w in recognized[i:j+complete])
            cuts.append(dict(start=max(0.,float(recognized[i]['start'])-.06),end=max(0.,float(recognized[j]['start'])-.06),
                reason='Repeated or abandoned attempt: '+' '.join(w['word'] for w in recognized[i:j]),
                confidence=.97 if confident else .7,approved=False))
            covered=j-1
            break
    return cuts


def run_media(command,cancel=None):
    process=subprocess.Popen([str(x) for x in command],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        while True:
            if cancel is not None and cancel.is_set():
                process.kill();process.communicate();raise RuntimeError('Export cancelled; source and existing exports retained.')
            try:
                _,errors=process.communicate(timeout=.25)
                break
            except subprocess.TimeoutExpired:pass
        if process.returncode:
            detail=errors.decode('utf-8','replace')
            if '-/filter_complex' in command and 'Unrecognized option' in detail:
                legacy=list(command);legacy[legacy.index('-/filter_complex')]='-filter_complex_script'
                return run_media(legacy,cancel)
            raise RuntimeError(detail[-2000:])
    finally:
        if process.poll() is None:process.kill();process.communicate()


def publish_file(source,destination):
    destination=Path(destination)
    fd,stage=tempfile.mkstemp(prefix='.'+destination.stem+'-',suffix='.partial',dir=destination.parent)
    try:
        with os.fdopen(fd,'wb') as out,Path(source).open('rb') as inp:
            shutil.copyfileobj(inp,out);out.flush();os.fsync(out.fileno())
        os.replace(stage,destination)
    finally:Path(stage).unlink(missing_ok=True)


def cut_media(source,target,segments,ffmpeg,directory,*,audio=True,alpha=False,cancel=None):
    filters=[]
    for index,(start,end) in enumerate(segments):
        filters.append(f'[0:v]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS[v{index}]')
        if audio:filters.append(f'[0:a]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS[a{index}]')
    inputs=''.join(f'[v{i}]'+(f'[a{i}]' if audio else '') for i in range(len(segments)))
    filters.append(inputs+f'concat=n={len(segments)}:v=1:a={int(audio)}[v]'+('[a]' if audio else ''))
    graph=Path(directory)/('cut-'+Path(target).stem+'.ffscript');graph.write_text(';'.join(filters),encoding='utf-8')
    command=[ffmpeg,'-y','-v','error','-i',str(source),'-/filter_complex',str(graph),'-map','[v]']
    if audio:command+=['-map','[a]','-c:a','aac','-b:a','160k']
    command+=['-c:v','qtrle','-pix_fmt','argb'] if alpha else ['-c:v','libx264','-preset','veryfast','-crf','18','-pix_fmt','yuv420p']
    run_media(command+['-movflags','+faststart',str(target)],cancel)


def caption_events(data,groups):
    """Full script remains in the review; each approved word reveals at its start."""
    events=[(0.,'',0)]
    words=data['words'];cursor=0
    for number,group in enumerate(groups):
        count=len(group.split())
        for offset in range(count):
            if cursor+offset>=len(words):break
            prefix=' '.join(w['word'] for w in words[cursor:cursor+offset+1])
            events.append((float(words[cursor+offset]['start']),prefix,number))
        cursor+=count
    # Multiple zero-duration estimates share one frame; retain the complete prefix.
    merged={}
    for time,text,group in events:merged[min(float(data['duration']),time)]=(time,text,group)
    return [merged[t] for t in sorted(merged)]


def write_concat(events,images,duration,path):
    lines=['ffconcat version 1.0']
    for index,(time,*_) in enumerate(events):
        end=events[index+1][0] if index+1<len(events) else duration
        if end<=time:continue
        # Images are generated in this controlled directory with numeric names.
        lines.extend([f"file '{Path(images[index]).name}'",f'duration {end-time:.9f}'])
    lines.append(f"file '{Path(images[-1]).name}'")
    Path(path).write_text('\n'.join(lines)+'\n',encoding='utf-8')


def export_videos(data,concat,output_stem,ffmpeg,work,progress,cancel):
    validate_alignment(data,approved=True)
    source=Path(data['source']['scene']);foreground=data['source'].get('foreground')
    if not source.is_file():raise ValueError('Editable source is missing. Original recording has not been changed.')
    baseline=Path(str(output_stem)+'_baseline.mp4');edited=Path(str(output_stem)+'_edited.mp4')
    stage=Path(work)/'baseline.mp4'
    progress('Rendering approved text onto the baseline…')
    command=[ffmpeg,'-y','-v','error','-i',str(source),'-safe','0','-i',str(concat)]
    graph=f'[1:v]fps={data["source"]["fps"]},format=rgba[captions];[0:v][captions]overlay=eof_action=repeat:shortest=0[texted]'
    if foreground:
        command+=['-i',str(foreground)]
        alpha='premultiplied' if data['source'].get('foreground_premultiplied') else 'straight'
        graph+=f';[texted][2:v]overlay=eof_action=repeat:shortest=0:alpha={alpha}[v]'
    else:graph+=';[texted]null[v]'
    graphfile=Path(work)/'overlay.ffscript';graphfile.write_text(graph,encoding='utf-8')
    command+=['-/filter_complex',str(graphfile),'-map','[v]','-map','0:a?','-t',str(data['duration']),'-c:v','libx264',
              '-preset','veryfast','-crf','18','-pix_fmt','yuv420p','-c:a','copy','-movflags','+faststart',str(stage)]
    run_media(command,cancel)
    cuts=data.get('cuts',[])
    if data.get('advanced_mode','off')!='off':
        fps=float(data['source']['fps'])
        applied=[dict(c,start=round(float(c['start'])*fps)/fps,end=round(float(c['end'])*fps)/fps) for c in cuts]
        kept=complement(float(data['duration']),applied)
        progress('Applying approved advanced cuts…')
        advanced=Path(work)/'edited.mp4'
        if kept==[(0.,float(data['duration']))]:shutil.copyfile(stage,advanced)
        else:cut_media(stage,advanced,kept,ffmpeg,work,audio=data['source'].get('audio',True),cancel=cancel)
    if cancel.is_set():raise RuntimeError('Export cancelled; source retained.')
    publish_file(stage,baseline)
    result={'baseline':str(baseline)}
    if data.get('advanced_mode','off')!='off':
        publish_file(advanced,edited)
        from update_core import atomic_json
        report=remap_alignment(data,kept)
        for key in ('source','render_project','output_stem'):report.pop(key,None)
        report['kind']='edited_alignment_report'
        atomic_json(edited.with_suffix('.alignment.json'),report)
        atomic_json(edited.with_suffix('.edits.json'),dict(schema=1,baseline=str(baseline),cuts=cuts,applied_cuts=applied,kept_segments=kept))
        result['edited']=str(edited)
    return result
