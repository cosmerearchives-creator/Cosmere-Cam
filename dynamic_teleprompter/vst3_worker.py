# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent VST3 audio helper. See COPYING.GPL3 and VST3-HOST.md.

Protocol: uint32 LE JSON byte length, then UTF-8 JSON (maximum 16MiB).
Request is an array: [operation, plugin_path, plugin_name, parameters, base64_pcm].
Operations: inspect, process, self_test, close. PCM is mono s16le at 48000Hz.
Reply: {"ok": true, "value": ...}, or {"ok": false, "error": ...}.
This executable does not import or require the Suite GUI.
"""
import base64,json,os,struct,sys
MAX=16*1024*1024

def serve(inp,out):
    instances={}
    def exact(n):
        data=bytearray()
        while len(data)<n:
            block=inp.read(n-len(data))
            if not block:raise EOFError()
            data.extend(block)
        return bytes(data)
    while True:
        try:
            size=struct.unpack('<I',exact(4))[0]
            if size>MAX:raise ValueError('Request exceeded limit')
            message=json.loads(exact(size));op=message[0]
        except EOFError:return
        try:
            if op=='close':result=None
            else:
                import numpy as np
                import pedalboard
                if op=='self_test':
                    sample=np.ones((1,256),dtype=np.float32)
                    result=dict(version=pedalboard.__version__,gain_ok=bool(np.allclose(pedalboard.Gain(gain_db=-6)(sample,48000),10**(-6/20),atol=.001)))
                else:
                    _,path,name,*rest=message;key=(path,name)
                    if key not in instances:instances[key]=pedalboard.load_plugin(path,**({'plugin_name':name} if name else {}))
                    plugin=instances[key]
                    if op=='inspect':result={str(name):param.raw_value for name,param in plugin.parameters.items() if isinstance(param.raw_value,(str,int,float,bool))}
                    elif op=='process':
                        parameters,encoded=rest
                        for name,value in parameters.items():
                            if name in plugin.parameters:plugin.parameters[name].raw_value=value
                        raw=base64.b64decode(encoded,validate=True);samples=np.frombuffer(raw,dtype='<i2').astype(np.float32)/32768
                        processed=np.asarray(plugin(samples.reshape(1,-1),48000,reset=False),dtype=np.float32)
                        if processed.ndim==2:processed=processed.mean(axis=0)
                        processed=np.clip(np.nan_to_num(processed).reshape(-1),-1,1)
                        if len(processed)!=len(samples):raise RuntimeError('Plugin changed buffer size')
                        result=base64.b64encode(np.rint(processed*32767).astype('<i2').tobytes()).decode('ascii')
                    else:raise ValueError('Unknown operation')
            response=dict(ok=True,value=result)
        except Exception as error:response=dict(ok=False,error=str(error))
        payload=json.dumps(response,allow_nan=False).encode('utf-8')
        if len(payload)>MAX:raise ValueError('Reply exceeded limit')
        out.write(struct.pack('<I',len(payload))+payload);out.flush()
        if op=='close':return

if __name__=='__main__':
    # Vendor diagnostics must not contaminate the framed protocol on stdout.
    output=os.fdopen(os.dup(sys.stdout.fileno()),'wb',buffering=0)
    os.dup2(sys.stderr.fileno(),sys.stdout.fileno())
    if os.name=='nt':
        import ctypes,msvcrt
        kernel=ctypes.WinDLL('kernel32');kernel.SetStdHandle.argtypes=[ctypes.c_ulong,ctypes.c_void_p]
        kernel.SetStdHandle(0xfffffff5,msvcrt.get_osfhandle(sys.stderr.fileno()))
    serve(sys.stdin.buffer,output)
