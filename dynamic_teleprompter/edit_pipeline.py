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


def normalize_word(word):
    """Normalize punctuation for matching, preserving original display words."""
    import unicodedata
    return re.sub(r"[^\w]",'',unicodedata.normalize('NFKC',word).casefold())


def propose_retakes(script,recognized):
    """Find long/imperfect restarts from unique script anchors; uncertain cuts stay manual."""
    from collections import defaultdict
    from difflib import SequenceMatcher
    original=script.split();target=[normalize_word(w) for w in original]
    heard=[normalize_word(w.get('word','')) for w in recognized]
    if len(target)<3 or len(heard)<4:return []
    def similar(a,b):
        return a==b or (min(len(a),len(b))>=4 and SequenceMatcher(None,a,b).ratio()>=.78)
    anchors={}
    for width in (2,3):
        index=defaultdict(list)
        for i in range(len(target)-width+1):index[tuple(target[i:i+width])].append(i)
        for key,positions in index.items():
            if len(positions)==1:anchors[key]=positions[0]
    events=defaultdict(list)
    for i in range(len(heard)-1):
        for width in (2,3):
            key=tuple(heard[i:i+width])
            if key in anchors:events[(anchors[key],width)].append(i)
    candidates=[]
    for (anchor,width),occurrences in events.items():
        for i,j in zip(occurrences,occurrences[1:]):
            if j-i>160 or float(recognized[j]['start'])-float(recognized[i]['start'])>90:continue
            left,right,position=i,j,anchor;errors=0
            # Recover words before an anchor, including a few ASR mistakes,
            # without reaching back across a script sentence boundary.
            while position>0 and left>0 and right>i+1 and anchor-position<64:
                if re.search(r'[.!?][\"\u201d\u2019)]*$',original[position-1]):break
                x,y,z=heard[left-1],heard[right-1],target[position-1]
                if not (similar(x,z) or similar(y,z) or similar(x,y)):
                    errors+=1
                    if errors>2:break
                left-=1;right-=1;position-=1
            attempt=heard[left:right];reference=target[position:position+min(100,len(attempt)+12)]
            evidence=sum(block.size for block in SequenceMatcher(None,attempt,reference,autojunk=False).get_matching_blocks())
            if evidence<2 or evidence/max(1,len(attempt))<.45:continue
            retained=heard[right:right+min(100,max(12,len(attempt)+8))]
            blocks=SequenceMatcher(None,retained,reference,autojunk=False).get_matching_blocks()
            retained_evidence=sum(block.size for block in blocks)
            if retained_evidence<3:continue
            if not any(block.size>=width and block.a<=anchor-position+2 and block.b<=anchor-position+2 for block in blocks):continue
            exact=0
            while left+exact<right and right+exact<len(heard) and heard[left+exact]==heard[right+exact]:exact+=1
            gap=float(recognized[right]['start'])-float(recognized[right-1]['end']) if right else 0
            confident=(exact>=5 and gap>=.4 and evidence/max(1,len(attempt))>=.8
                       and retained_evidence>=evidence and all(float(w.get('conf',1))>=.85 for w in recognized[left:right+exact]))
            candidates.append(dict(first=left,second=right,position=position,confidence=.97 if confident else .7))
    # Prefer full-sentence restarts before inner stutters. Otherwise a small
    # repeated phrase inside a failed take can hide its larger restart chain.
    boundary=lambda item:item['position']==0 or bool(re.search(r'[.!?][\"\u201d\u2019)]*$',original[item['position']-1]))
    accepted=[]
    for full_sentence in (True,False):
        for item in sorted(candidates,key=lambda item:(item['second'],item['first'])):
            if boundary(item)!=full_sentence:continue
            if any(item['first']<other['second'] and item['second']>other['first'] for other in accepted):continue
            accepted.append(item)
    selected=[]
    for item in sorted(accepted,key=lambda item:item['first']):
        left,right=item['first'],item['second']
        cut=dict(start=max(0.,float(recognized[left]['start'])-.06),end=max(0.,float(recognized[right]['start'])-.06),
                 reason='Restarted passage: '+' '.join(original[item['position']:item['position']+10]),
                 confidence=item['confidence'],approved=False)
        if selected and abs(selected[-1]['end']-cut['start'])<.12:
            selected[-1]['end']=cut['end'];selected[-1]['confidence']=min(selected[-1]['confidence'],cut['confidence'])
        else:selected.append(cut)
    return selected


def run_media(command,cancel=None):
    from processing_session import current
    session=current()
    if session is not None:return session.run(command,cancel=cancel,duration=getattr(session,'duration',None))
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


def keep_filter(segments,audio=True):
    """One video pass avoids full-resolution buffering across N concat branches."""
    import math
    if not segments:raise ValueError('No retained recording sections.')
    previous=0.
    for start,end in segments:
        if not math.isfinite(start+end) or start<previous or end<=start:raise ValueError('Invalid trim sections.')
        previous=end
    select='+'.join(f'gte(t,{a:.9f})*lt(t,{b:.9f})' for a,b in segments)
    gaps=[f'gte(T,{segments[i][0]:.9f})*{segments[i][0]-segments[i-1][1]:.9f}' for i in range(1,len(segments))]
    offset='+'.join(gaps) or '0'
    filters=[f"[0:v]select='{select}',setpts='PTS-STARTPTS-({offset})/TB'[v]"]
    if audio:
        for i,(a,b) in enumerate(segments):filters.append(f'[0:a]atrim=start={a:.9f}:end={b:.9f},asetpts=PTS-STARTPTS[a{i}]')
        filters.append(''.join(f'[a{i}]' for i in range(len(segments)))+f'concat=n={len(segments)}:v=0:a=1[a]')
    return ';'.join(filters)


def cut_media(source,target,segments,ffmpeg,directory,*,audio=True,alpha=False,premultiplied=False,cancel=None):
    from processing_session import current
    if current():current().duration=sum(b-a for a,b in segments)
    graph=Path(directory)/('cut-'+Path(target).stem+'.ffscript');graph.write_text(keep_filter(segments,audio),encoding='utf-8')
    from recorder import supports_alpha_conversion
    tagged=alpha and premultiplied and supports_alpha_conversion(ffmpeg)
    command=[ffmpeg,'-y','-v','error']+(['-alpha_mode','premultiplied'] if tagged else [])+['-i',str(source),'-/filter_complex',str(graph),'-map','[v]']
    if audio:command+=['-map','[a]','-c:a','aac','-b:a','160k']
    command+=['-c:v','qtrle','-pix_fmt','argb'] if alpha else ['-c:v','libx264','-preset','veryfast','-crf','18','-pix_fmt','yuv420p']
    if tagged:command+=['-alpha_mode','premultiplied']
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
        from recorder import supports_alpha_conversion
        premult=data['source'].get('foreground_premultiplied',False)
        tagged=premult and supports_alpha_conversion(ffmpeg)
        command+=(['-alpha_mode','premultiplied'] if tagged else [])+['-i',str(foreground)]
        alpha='premultiplied' if tagged else 'straight'
        if premult and not tagged:
            graph+=';[2:v]unpremultiply=inplace=1[fore]'
            fg='[fore]'
        else:fg='[2:v]'
        graph+=f';[texted]{fg}overlay=eof_action=repeat:shortest=0:format=rgb:alpha={alpha}[v]'
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


def preview_join(data,cut,ffmpeg,directory,cancel):
    """Preview the same frame-aligned join with captured foreground media intact."""
    root=Path(directory);fps=float(data['source']['fps'])
    a=round(float(cut['start'])*fps)/fps;b=round(float(cut['end'])*fps)/fps
    segments=[(max(0.,a-1.5),a),(b,min(float(data['duration']),b+1.5))]
    segments=[(start,end) for start,end in segments if end-start>.03]
    if not segments:raise ValueError('No surrounding footage available to preview.')
    scene=root/'preview-scene.mp4'
    cut_media(data['source']['scene'],scene,segments,ffmpeg,root,audio=data['source'].get('audio',True),cancel=cancel)
    foreground=data['source'].get('foreground')
    if not foreground:return scene
    fore=root/'preview-foreground.mov'
    cut_media(foreground,fore,segments,ffmpeg,root,audio=False,alpha=True,premultiplied=data['source'].get('foreground_premultiplied',False),cancel=cancel)
    result=root/'join.mp4'
    from recorder import supports_alpha_conversion
    premult=data['source'].get('foreground_premultiplied',False)
    tagged=premult and supports_alpha_conversion(ffmpeg)
    graph=''
    if premult and not tagged:graph='[1:v]unpremultiply=inplace=1[fore];'
    fg='[fore]' if premult and not tagged else '[1:v]'
    alpha='premultiplied' if tagged else 'straight'
    graph+=f'[0:v]{fg}overlay=shortest=1:format=rgb:alpha={alpha}[v]'
    command=[ffmpeg,'-y','-v','error','-i',str(scene)]+(['-alpha_mode','premultiplied'] if tagged else [])+['-i',str(fore),'-filter_complex',graph,'-map','[v]','-map','0:a?',
        '-c:v','libx264','-preset','veryfast','-crf','18','-pix_fmt','yuv420p','-c:a','copy',str(result)]
    run_media(command,cancel)
    return result
