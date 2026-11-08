"""Persistent stage timing, FFmpeg progress and retained failure evidence."""
import contextlib,contextvars,json,os,shutil,subprocess,threading,time
from datetime import datetime,timezone
from pathlib import Path
from functools import lru_cache
from update_core import atomic_json

_current=contextvars.ContextVar('processing_session',default=None)
def current():return _current.get()
@contextlib.contextmanager
def active(session):
    token=_current.set(session)
    try:yield session
    finally:_current.reset(token)

@lru_cache(maxsize=8)
def executable_version(executable):
    try:
        result=subprocess.run([str(executable),'-version'],capture_output=True,timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return result.stdout.decode('utf-8','replace').splitlines()[0]
    except Exception as error:return str(error)

class ProcessingSession:
    def __init__(self,directory,callback=None):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.path=self.directory/'processing.json';self.callback=callback
        try:self.report=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):self.report=dict(schema=1,stages=[],commands=[],active_processing_seconds=0.,review_wait_seconds=0.)
        self.started=time.monotonic();self.stage_started=self.started;self.stage=None
        self.state=dict(stage='Preparing recording',percent=None,elapsed_seconds=0.,stage_seconds=0.,stalled=False)
        self._last_save=0.
    def save(self):
        self.report['progress']=dict(self.state)
        atomic_json(self.path,self.report)
    def update(self,percent=None,**values):
        now=time.monotonic();self.state.update(values,percent=percent,elapsed_seconds=round(now-self.started,2),stage_seconds=round(now-self.stage_started,2))
        if self.callback:self.callback(dict(self.state))
        if now-self._last_save>1:
            self._last_save=now;self.save()
    def begin(self,name):
        self.end_stage();self.stage=name;self.stage_started=time.monotonic()
        self.update(stage=name,stalled=False)
    def end_stage(self):
        if self.stage is not None:
            self.report['stages'].append(dict(stage=self.stage,seconds=round(time.monotonic()-self.stage_started,3)))
            self.stage=None
    def finish(self,status='complete'):
        self.end_stage();self.report['active_processing_seconds']+=round(time.monotonic()-self.started,3)
        self.report.update(status=status,updated_utc=datetime.now(timezone.utc).isoformat())
        if status=='review' or (self.report.get('kind')=='export' and status in ('failed','cancelled')):self.report['review_ready_epoch']=time.time()
        self.update(100 if status in ('complete','review') else None,status=status);self.save()
        diagnostic=self.report.get('diagnostics_path')
        if diagnostic and Path(diagnostic).is_file():
            try:
                data=json.loads(Path(diagnostic).read_text(encoding='utf-8'));data['post_processing']=self.report
                atomic_json(diagnostic,data)
            except (OSError,ValueError):pass
    def begin_export(self):
        self.report['status']='exporting';self.report['kind']='export'
        ready=self.report.pop('review_ready_epoch',None)
        if ready:self.report['review_wait_seconds']+=round(max(0,time.time()-ready),3)
        self.begin('Preparing approved text')
    def retain(self,file,label=None):
        source=Path(file)
        if source.is_file():
            target=self.directory/(label or source.name)
            if source.resolve()!=target.resolve():shutil.copyfile(source,target)
    def run(self,command,*,duration=None,cancel=None,stall_timeout=180,**ignored):
        command=[str(x) for x in command];number=len(self.report['commands'])+1
        name=f'{number:03d}';log=self.directory/(name+'-ffmpeg.log')
        for flag in ('-/filter_complex','-filter_complex_script'):
            if flag in command:self.retain(command[command.index(flag)+1],name+'-filters.ffscript')
        row=dict(stage=self.state['stage'],command=command,ffmpeg_version=executable_version(command[0]),log=str(log),expected_duration=duration,started_utc=datetime.now(timezone.utc).isoformat())
        self.report['commands'].append(row);self.save()
        # stdout is exclusively the progress protocol. stderr is retained verbatim.
        actual=command[:1]+['-progress','pipe:1','-nostats','-stats_period','0.5']+command[1:]
        if '-loglevel' in actual:actual[actual.index('-loglevel')+1]='info'
        elif '-v' in actual:actual[actual.index('-v')+1]='info'
        reader=None;process=None;last_activity=time.monotonic();last_position=-1.;lock=threading.Lock();values={}
        started=time.monotonic();error=None
        outputs=[Path(value) for value in command[1:] if Path(value).suffix.lower() in ('.mp4','.mov','.mkv','.wav','.webm') and not value.startswith('-')]
        try:
            with log.open('wb') as stderr:
                process=subprocess.Popen(actual,stdout=subprocess.PIPE,stderr=stderr,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                def read_progress():
                    nonlocal last_activity,last_position
                    for raw in iter(process.stdout.readline,b''):
                        line=raw.decode('utf-8','replace').strip()
                        if '=' not in line:continue
                        key,value=line.split('=',1)
                        with lock:
                            values[key]=value
                            if key in ('out_time_us','frame'):
                                try:position=float(value)
                                except ValueError:continue
                                if position>last_position if key=='out_time_us' else position>float(row.get('frame',-1)):
                                    last_activity=time.monotonic()
                                if key=='out_time_us':last_position=max(last_position,position)
                                else:row['frame']=position
                reader=threading.Thread(target=read_progress,daemon=True);reader.start()
                while process.poll() is None:
                    now=time.monotonic()
                    if cancel is not None and cancel.is_set():raise RuntimeError('Processing cancelled; source take retained.')
                    if now-last_activity>stall_timeout:raise TimeoutError(f'No FFmpeg progress for {stall_timeout} seconds; source retained. See {log}')
                    with lock:sample=dict(values)
                    try:seconds=max(0,float(sample.get('out_time_us',0) or 0)/1_000_000)
                    except ValueError:seconds=0
                    percent=min(99.,seconds/duration*100) if duration and duration>0 else None
                    row.update(progress=sample,seconds=round(now-started,2),free_disk_bytes=shutil.disk_usage(self.directory).free,
                               output_sizes={str(path):path.stat().st_size for path in outputs if path.is_file()})
                    self.update(percent,speed=sample.get('speed',''),frame=sample.get('frame',''),stalled=now-last_activity>15,last_progress_seconds_ago=round(now-last_activity,1))
                    time.sleep(.2)
                reader.join(timeout=2)
                process.stdout.close()
            details=log.read_bytes()
            if process.returncode:
                text=details.decode('utf-8','replace')
                if '-/filter_complex' in command and 'Unrecognized option' in text:
                    legacy=list(command);legacy[legacy.index('-/filter_complex')]='-filter_complex_script'
                    return self.run(legacy,duration=duration,cancel=cancel,stall_timeout=stall_timeout)
                raise RuntimeError(text[-3000:])
            self.update(100)
            return subprocess.CompletedProcess(command,0,b'',details)
        except Exception as exc:
            error=str(exc);raise
        finally:
            if process is not None and process.poll() is None:
                process.kill();process.wait(timeout=10)
            if reader is not None and reader.is_alive():reader.join(timeout=2)
            if process is not None and process.stdout is not None and (reader is None or not reader.is_alive()):process.stdout.close()
            row.update(seconds=round(time.monotonic()-started,3),returncode=process.returncode if process else None,error=error)
            self.save()
