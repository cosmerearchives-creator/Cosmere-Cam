"""Portable project ZIPs contain only explicitly referenced media/model data."""
import hashlib,json,os,tempfile,zipfile
from pathlib import Path,PurePosixPath
from copy import deepcopy

MANIFEST='project-manifest.json'
PROJECT='project.teleprompter.json'
FORBIDDEN={'.exe','.dll','.pyd','.vst3','.zip','.7z','.bat','.ps1','.py','.msi'}
def key(path):return os.path.normcase(str(Path(path).resolve()))
def relative_file(root,name):
    part=PurePosixPath(str(name))
    if not name or part.is_absolute() or '..' in part.parts or '\\' in str(name) or ':' in str(name):raise ValueError('Unsafe package path: '+str(name))
    path=(root/Path(*part.parts)).resolve()
    if not path.is_relative_to(root.resolve()):raise ValueError('Package asset escapes its source folder.')
    return path

def references(data):
    settings=data.get('settings',{})
    if settings.get('background_image'):yield settings['background_image'],False
    for layer in settings.get('media_layers',[]):
        if layer.get('type') in ('image','video','live2d') and layer.get('path'):yield layer['path'],layer.get('type')=='live2d'
        for value in layer.get('movement',{}).get('direction_models',{}).values():
            if value:yield value,True
    for model in data.get('model_library',[]):
        if model.get('path'):yield model['path'],True

def transform_paths(data,mapping):
    def walk(value,field=''):
        if isinstance(value,dict):return {k:walk(v,k if field!='direction_models' else 'path') for k,v in value.items()}
        if isinstance(value,list):return [walk(item,field) for item in value]
        if isinstance(value,str) and value and field in ('path','background_image'):
            return mapping.get(value,mapping.get(key(value),value))
        return value
    return walk(data)

def model_dependencies(manifest):
    data=json.loads(manifest.read_text(encoding='utf-8-sig'));refs=data.get('FileReferences',{})
    values=[refs.get(name) for name in ('Moc','Physics','Pose','DisplayInfo','UserData')]
    values+=list(refs.get('Textures',[]))
    values+=[item.get('File') for item in refs.get('Expressions',[])]
    for motions in refs.get('Motions',{}).values():
        for item in motions:values.extend([item.get('File'),item.get('Sound')])
    for value in values:
        if not value:continue
        normalized=str(value).replace('\\','/')
        path=relative_file(manifest.parent,normalized)
        yield path,normalized

def plan_project(data):
    if data.get('format')!='DynamicTeleprompterProject':raise ValueError('Invalid project format.')
    project=deepcopy(data);files={};mapping={};missing=[]
    def add(source,destination):
        source=Path(source)
        if source.suffix.lower() in FORBIDDEN:raise ValueError('Executable/plugin/runtime files are external requirements: '+source.name)
        if not source.is_file():missing.append(str(source));return
        if source.is_symlink():raise ValueError('Select regular asset files rather than symbolic links: '+str(source))
        files[destination]=dict(source=str(source.resolve()),size=source.stat().st_size,mtime=source.stat().st_mtime_ns)
    for value,model in references(project):
        source=Path(value).resolve();identity=hashlib.sha256(key(source).encode()).hexdigest()[:12]
        if model:
            destination='assets/'+identity+'/'+source.name
            add(source,destination)
            if source.is_file():
                for dependency,relative in model_dependencies(source):add(dependency,'assets/'+identity+'/'+relative)
        else:
            destination='assets/'+identity+'-'+source.name;add(source,destination)
        mapping[value]=destination;mapping[key(source)]=destination
    if missing:raise ValueError('Missing project assets:\n'+'\n'.join(sorted(set(missing))[:50]))
    project=transform_paths(project,mapping);external=[]
    for entry in project.get('settings',{}).get('audio_chain',[]):
        if entry.get('type') in ('vst2','vst3'):
            filename=Path(entry.get('path','')).name
            external.append(dict(type=entry['type'],name=filename))
            entry['path']=filename;entry['requested_enabled']=entry.get('enabled',False);entry['enabled']=False
    return dict(project=project,files=files,total_bytes=sum(item['size'] for item in files.values()),external_requirements=external)

def export_project(plan,destination,progress=lambda value:None,cancel=None):
    destination=Path(destination)
    if key(destination) in {key(item['source']) for item in plan['files'].values()}:raise ValueError('Export cannot overwrite a project asset.')
    fd,temporary=tempfile.mkstemp(prefix='.'+destination.stem+'-',suffix='.partial',dir=destination.parent);os.close(fd)
    total=max(1,plan['total_bytes']);written=0;assets=[]
    try:
        with zipfile.ZipFile(temporary,'w',allowZip64=True) as archive:
            for relative,item in plan['files'].items():
                if cancel is not None and cancel.is_set():raise RuntimeError('Project export cancelled; previous files retained.')
                source=Path(item['source']);before=source.stat();digest=hashlib.sha256();count=0
                info=zipfile.ZipInfo(relative);info.compress_type=zipfile.ZIP_STORED
                with source.open('rb') as inp,archive.open(info,'w',force_zip64=True) as out:
                    while True:
                        block=inp.read(1024*1024)
                        if not block:break
                        if cancel is not None and cancel.is_set():raise RuntimeError('Project export cancelled; previous files retained.')
                        out.write(block);digest.update(block);count+=len(block);written+=len(block);progress(min(99,int(written/total*100)))
                after=source.stat()
                if before.st_mtime_ns!=after.st_mtime_ns or before.st_size!=after.st_size or count!=item['size']:raise RuntimeError('Asset changed during export: '+source.name)
                assets.append(dict(path=relative,size=count,sha256=digest.hexdigest()))
            archive.writestr(PROJECT,json.dumps(plan['project'],ensure_ascii=False,indent=2))
            archive.writestr(MANIFEST,json.dumps(dict(schema=1,project_file=PROJECT,assets=assets,external_requirements=plan['external_requirements']),indent=2))
        os.replace(temporary,destination);progress(100);return str(destination)
    finally:Path(temporary).unlink(missing_ok=True)

def load_exported_project(folder,progress=lambda value:None,cancel=None):
    root=Path(folder).resolve();manifest=relative_file(root,MANIFEST)
    if not manifest.is_file():raise ValueError('Select the extracted export folder containing '+MANIFEST)
    data=json.loads(manifest.read_text(encoding='utf-8'))
    if data.get('schema')!=1 or len(data.get('assets',[]))>50000:raise ValueError('Invalid project manifest.')
    project=json.loads(relative_file(root,data['project_file']).read_text(encoding='utf-8'))
    if project.get('format')!='DynamicTeleprompterProject':raise ValueError('Invalid exported project.')
    mapping={};warnings=[];assets=data['assets']
    for index,item in enumerate(assets):
        relative=item['path'];path=relative_file(root,relative)
        if path.suffix.lower() in FORBIDDEN:raise ValueError('Executable files are not project media.')
        if not path.is_file():raise ValueError('Missing exported asset: '+relative)
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            while True:
                if cancel is not None and cancel.is_set():raise RuntimeError('Project import cancelled.')
                block=stream.read(1024*1024)
                if not block:break
                digest.update(block)
        if digest.hexdigest()!=item['sha256']:warnings.append('Changed since export: '+relative)
        mapping[relative]=str(path);progress(int((index+1)/max(1,len(assets))*100))
    restored=transform_paths(project,mapping)
    # Verify model references stay inside their packaged model directory too.
    for value,is_model in references(restored):
        path=Path(value).resolve()
        if not path.is_relative_to(root) or not path.is_file():raise ValueError('Unresolved project media: '+str(value))
        if is_model:
            for dependency,_ in model_dependencies(path):
                if not dependency.is_file():raise ValueError('Missing model dependency: '+str(dependency))
    return restored,warnings
