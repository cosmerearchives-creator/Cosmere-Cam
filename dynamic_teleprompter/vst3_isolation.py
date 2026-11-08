"""Local client for the separately executable GPL VST3 host; no Pedalboard import."""
import base64,importlib.util,json,os,queue,struct,subprocess,sys,threading,time
from pathlib import Path
from audio_diagnostics import observe
_TIMEOUT=.35
_START_TIMEOUT=8.
_INSPECTION_TIMEOUT=8.
_thread_state=threading.local()
MAX=16*1024*1024

def command():
    here=Path(__file__).resolve().parent
    choices=[here/'native/vst3/CosmereVST3Host.exe',Path(sys.executable).parent/'_internal/native/vst3/CosmereVST3Host.exe',here.parent/'dist/CosmereVST3Host/CosmereVST3Host.exe']
    found=next((str(path) for path in choices if path.is_file()),None)
    if found:return [found]
    if not getattr(sys,'frozen',False) and importlib.util.find_spec('pedalboard') is not None:return [sys.executable,str(here/'vst3_worker.py')]
    raise RuntimeError('The separate VST3 host is not installed.')
def available():
    try:command();return True
    except RuntimeError:return False

class Host:
    def __init__(self):
        self.process=subprocess.Popen(command(),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        import windows_jobs
        try:self.job=windows_jobs.contain(self.process)
        except Exception:
            self.process.kill();self.process.wait(timeout=2);raise
        self.lock=threading.Lock();self.replies=queue.Queue(maxsize=4);self.requests=queue.Queue(maxsize=1);self.first_request=True
        self.reader=threading.Thread(target=self.read,daemon=True);self.writer=threading.Thread(target=self.write,daemon=True)
        self.reader.start();self.writer.start()
    def exact(self,n):
        data=bytearray()
        while len(data)<n:
            block=self.process.stdout.read(n-len(data))
            if not block:raise RuntimeError('VST3 host exited')
            data.extend(block)
        return bytes(data)
    def read(self):
        try:
            while True:
                size=struct.unpack('<I',self.exact(4))[0]
                if size>MAX:raise RuntimeError('VST3 response exceeded limit')
                self.replies.put(json.loads(self.exact(size)),timeout=.5)
        except Exception as error:
            try:self.replies.put_nowait(error)
            except queue.Full:pass
    def write(self):
        while self.process.poll() is None:
            try:data=self.requests.get(timeout=.1)
            except queue.Empty:continue
            try:self.process.stdin.write(data);self.process.stdin.flush()
            except OSError:return
    def request(self,msg,timeout):
        timeout=max(timeout,_START_TIMEOUT) if self.first_request else timeout
        deadline=time.monotonic()+timeout;acquired=self.lock.acquire(timeout=timeout)
        try:
            if not acquired:raise queue.Empty()
            message=list(msg)
            if message[0]=='process':message[-1]=base64.b64encode(message[-1]).decode('ascii')
            payload=json.dumps(message,allow_nan=False).encode('utf-8')
            if len(payload)>MAX:raise RuntimeError('VST3 request exceeded limit')
            self.requests.put_nowait(struct.pack('<I',len(payload))+payload)
            result=self.replies.get(timeout=max(0,deadline-time.monotonic()))
            if isinstance(result,Exception):raise result
            self.first_request=False
            if not result.get('ok'):raise RuntimeError(result.get('error','VST3 error'))
            value=result['value']
            return base64.b64decode(value,validate=True) if message[0]=='process' else value
        except (queue.Empty,queue.Full,OSError,ValueError) as error:
            self.process.kill();raise RuntimeError('VST3 host timed out or stopped responding') from error
        finally:
            if acquired:self.lock.release()
    def close(self):
        try:
            if self.process.poll() is None:
                try:self.request(('close',),.3);self.process.wait(timeout=.5)
                except Exception:self.process.kill();self.process.wait(timeout=2)
        finally:
            self.reader.join(timeout=1);self.writer.join(timeout=1)
            for pipe,worker in ((self.process.stdin,self.writer),(self.process.stdout,self.reader)):
                if not worker.is_alive():
                    try:pipe.close()
                    except OSError:pass
            import windows_jobs
            windows_jobs.release(self.job)

def _get_host():
    host = getattr(_thread_state, 'host', None)
    if host is None or host.process.poll() is not None:
        if host is not None:
            host.close()
        host = Host()
        _thread_state.host = host
    return host


def process(path, plugin_name, parameters, data):
    path = str(Path(path))
    if not Path(path).exists():
        raise FileNotFoundError('VST3 not found: ' + path)
    # If a plugin fails, avoid restarting its host on every 10ms audio chunk.
    cooldown = getattr(_thread_state, 'cooldown', {})
    retry_time = cooldown.get((path, plugin_name), 0)
    if time.monotonic() < retry_time:
        raise RuntimeError('VST3 temporarily bypassed after host failure')
    try:
        started = time.perf_counter()
        result = _get_host().request(('process', path, plugin_name, parameters, data), _TIMEOUT)
        observe('vst_host_roundtrip', (time.perf_counter() - started) * 1000, len(data) / 2 / 48000 * 1000)
        cooldown.pop((path, plugin_name), None)
        return result
    except (RuntimeError, TimeoutError, OSError) as exc:
        cooldown[(path, plugin_name)] = time.monotonic() + 5
        _thread_state.cooldown = cooldown
        host = getattr(_thread_state, 'host', None)
        if host:
            host.close()
            _thread_state.host = None
        raise exc


def inspect(path, plugin_name=''):
    # Inspection uses a short-lived separate process, even when monitoring is active.
    host = Host()
    try:
        return host.request(('inspect', str(path), str(plugin_name)), _INSPECTION_TIMEOUT)
    finally:
        host.close()

