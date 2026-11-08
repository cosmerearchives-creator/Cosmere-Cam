"""Experimental Cubism Core model mesh viewer for static poses.

Uses an SDK-installed official Cubism Core DLL. Meshes are drawn via Qt;
Cubism advanced clipping masks, shader effects, physics and motions are not
implemented. This is not a substitute for the official Cubism renderer.
"""
import ctypes as C
import os
import json
import math
import threading
import time
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QPointF
from PySide6.QtGui import QPainter, QPainterPath, QImage, QTransform
from live2d_idle import animation_values, microphone


class Vector2(C.Structure):
    _fields_ = [('X', C.c_float), ('Y', C.c_float)]


def _pointer(dll, name, result, *args):
    func = getattr(dll, name)
    func.restype = result
    func.argtypes = list(args)
    return func


class CubismMeshModel:
    def __init__(self, path, dll):
        self.path = Path(path).resolve()
        model_json = json.loads(self.path.read_text(encoding='utf-8-sig'))
        refs = model_json['FileReferences']
        self.textures = [QImage(str((self.path.parent / t).resolve())).convertToFormat(QImage.Format.Format_ARGB32)
                         for t in refs['Textures']]
        if any(t.isNull() for t in self.textures):
            raise ValueError('Could not load one or more model textures')
        blob = (self.path.parent / refs['Moc']).read_bytes()
        # Cubism Core requires 64-byte aligned, writable, long-lived moc memory.
        self._moc_bytes = C.create_string_buffer(len(blob) + 64)
        self._moc_address = (C.addressof(self._moc_bytes) + 63) & ~63
        C.memmove(self._moc_address, blob, len(blob))
        self._moc = dll.csmReviveMocInPlace(self._moc_address, len(blob))
        if not self._moc:
            raise ValueError('Cubism Core rejected this moc3 model')
        size = dll.csmGetSizeofModel(self._moc)
        self._model_bytes = C.create_string_buffer(size + 16)
        self._model_address = (C.addressof(self._model_bytes) + 15) & ~15
        self._model = dll.csmInitializeModelInPlace(self._moc, self._model_address, size)
        if not self._model:
            raise ValueError('Cubism Core could not initialize the model')
        dll.csmUpdateModel(self._model)
        self.dll = dll
        # Tracking must not prevent the previously working static renderer from loading.
        # Some Cubism Core builds/models may reject parameter introspection.
        self.parameter_indices = {}
        self.parameter_defaults = []
        self.parameter_min = None
        self.parameter_max = None
        self.parameter_values = None
        self.last_pose = None
        self.previous_controlled = set()
        self._next_idle_update = 0.0
        try:
            count_params = dll.csmGetParameterCount(self._model)
            ids = dll.csmGetParameterIds(self._model)
            self.parameter_indices = {ids[i].decode('utf-8', 'replace'): i for i in range(count_params)}
            defaults = dll.csmGetParameterDefaultValues(self._model)
            self.parameter_defaults = [defaults[i] for i in range(count_params)]
            self.parameter_min = dll.csmGetParameterMinimumValues(self._model)
            self.parameter_max = dll.csmGetParameterMaximumValues(self._model)
            self.parameter_values = dll.csmGetParameterValues(self._model)
        except Exception as exc:
            print(f'Live2D tracking unavailable; static renderer active: {exc}', flush=True)
        self.count = dll.csmGetDrawableCount(self._model)
        self.canvas = Vector2()
        self.origin = Vector2()
        self.ppu = C.c_float()
        dll.csmReadCanvasInfo(self._model, C.byref(self.canvas), C.byref(self.origin), C.byref(self.ppu))
        if self.canvas.X <= 0 or self.canvas.Y <= 0 or self.ppu.value <= 0:
            raise ValueError('Invalid Cubism canvas geometry')

    def set_tracking(self, values):
        """Update parameters on the rendering thread only; clamp to model limits."""
        if not self.parameter_indices or self.parameter_values is None:
            return False
        pose = tuple(sorted((str(k), round(float(v), 3)) for k, v in values.items() if k in self.parameter_indices))
        if pose == self.last_pose or (not pose and self.last_pose is None):
            return False
        controlled = {pid for pid, _ in pose}
        for pid in self.previous_controlled - controlled:
            index = self.parameter_indices[pid]
            self.parameter_values[index] = self.parameter_defaults[index]
        for pid, value in pose:
            index = self.parameter_indices.get(pid)
            if index is not None:
                self.parameter_values[index] = max(self.parameter_min[index], min(self.parameter_max[index], value))
        self.previous_controlled = controlled
        self.dll.csmUpdateModel(self._model)
        self.last_pose = pose
        return True

    def draw(self, painter, canvas_width, canvas_height):
        """Render static Cubism geometry with basic clipping masks and blend modes.

        This is a Qt CPU fallback, not full compatibility with Cubism's GPU renderer.
        The finished composite is cached by draw_live2d between frames.
        """
        d, m, n = self.dll, self._model, self.count
        tex = d.csmGetDrawableTextureIndices(m)
        orders = d.csmGetRenderOrders(m)
        opacities = d.csmGetDrawableOpacities(m)
        vertex_counts = d.csmGetDrawableVertexCounts(m)
        vertices = d.csmGetDrawableVertexPositions(m)
        uvs = d.csmGetDrawableVertexUvs(m)
        index_counts = d.csmGetDrawableIndexCounts(m)
        indices = d.csmGetDrawableIndices(m)
        mask_counts = d.csmGetDrawableMaskCounts(m)
        masks = d.csmGetDrawableMasks(m)
        flags = d.csmGetDrawableConstantFlags(m)
        factor = min(canvas_width / self.canvas.X, canvas_height / self.canvas.Y)

        def draw_mesh(target, i, alpha=1.0):
            texture_index = tex[i]
            if texture_index < 0 or texture_index >= len(self.textures):
                return
            if vertex_counts[i] <= 0 or opacities[i] <= 0:
                return
            source = self.textures[texture_index]
            sw, sh = source.width(), source.height()
            count, v, uv, ind = vertex_counts[i], vertices[i], uvs[i], indices[i]
            if not v or not uv or not ind or sw == 0 or sh == 0:
                return
            def world(k):
                return QPointF(canvas_width/2 + (v[k].X * self.ppu.value + self.origin.X - self.canvas.X/2) * factor,
                               canvas_height/2 - (v[k].Y * self.ppu.value + self.origin.Y - self.canvas.Y/2) * factor)
            def image_pt(k):
                return QPointF(uv[k].X * sw, (1 - uv[k].Y) * sh)
            for t in range(0, index_counts[i] - 2, 3):
                a, b, c = int(ind[t]), int(ind[t+1]), int(ind[t+2])
                if min(a,b,c) < 0 or max(a,b,c) >= count:
                    continue
                p1,p2,p3 = world(a),world(b),world(c)
                s1,s2,s3 = image_pt(a),image_pt(b),image_pt(c)
                ux,uy = s2.x()-s1.x(),s2.y()-s1.y()
                vx,vy = s3.x()-s1.x(),s3.y()-s1.y()
                det = ux*vy - uy*vx
                if abs(det) < 0.00001: continue
                ax,ay = p2.x()-p1.x(),p2.y()-p1.y()
                bx,by = p3.x()-p1.x(),p3.y()-p1.y()
                m11=(ax*vy-bx*uy)/det
                m21=(bx*ux-ax*vx)/det
                m12=(ay*vy-by*uy)/det
                m22=(by*ux-ay*vx)/det
                dx=p1.x()-m11*s1.x()-m21*s1.y()
                dy=p1.y()-m12*s1.x()-m22*s1.y()
                target.save()
                target.setOpacity(max(0, min(1, alpha)))
                path=QPainterPath(p1)
                path.lineTo(p2);path.lineTo(p3);path.closeSubpath()
                target.setClipPath(path, Qt.ClipOperation.IntersectClip)
                target.setWorldTransform(QTransform(m11,m12,m21,m22,dx,dy), True)
                target.drawImage(QPointF(0,0), source)
                target.restore()

        def blank():
            image = QImage(int(canvas_width), int(canvas_height), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            return image

        mask_cache = {}
        # Build one alpha mask for every unique mask set, even if its source
        # drawable is behind or hidden in the normal render order.
        def mask_image(i):
            ids = tuple(int(masks[i][j]) for j in range(mask_counts[i]))
            if ids not in mask_cache:
                image = blank()
                mp = QPainter(image)
                try:
                    for drawable_index in ids:
                        if 0 <= drawable_index < n:
                            draw_mesh(mp, drawable_index, float(opacities[drawable_index]))
                finally:
                    mp.end()
                mask_cache[ids] = image
            return mask_cache[ids]

        for i in sorted(range(n), key=lambda j: orders[j]):
            if opacities[i] <= 0:
                continue
            has_mask = mask_counts[i] > 0
            if has_mask:
                piece = blank()
                pp = QPainter(piece)
                try:
                    draw_mesh(pp, i, float(opacities[i]))
                    pp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn
                                          if not (flags[i] & 8) else
                                          QPainter.CompositionMode.CompositionMode_DestinationOut)
                    pp.drawImage(QPointF(0,0), mask_image(i))
                finally:
                    pp.end()
            painter.save()
            try:
                if flags[i] & 1:
                    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
                elif flags[i] & 2:
                    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
                if has_mask:
                    painter.drawImage(QPointF(0,0), piece)
                else:
                    draw_mesh(painter, i, float(opacities[i]))
            finally:
                painter.restore()


def cubism_core_paths():
    """Search user-selected, shared user runtime, then legacy per-release paths."""
    candidates = []
    custom = os.environ.get('LIVE2D_CORE_PATH', '').strip().strip('"')
    if custom:
        selected = Path(custom).expanduser()
        candidates.append(selected / 'Live2DCubismCore.dll' if selected.is_dir() else selected)
    localappdata = os.environ.get('LOCALAPPDATA')
    if localappdata:
        candidates.append(Path(localappdata) / 'DynamicTeleprompter' / 'Runtime' / 'Live2D' / 'Live2DCubismCore.dll')
    candidates.append(Path(__file__).resolve().parent / 'live2d_sdk' / 'Live2DCubismCore.dll')
    candidates.append(Path(__file__).resolve().parent / 'Live2DCubismCore.dll')
    return candidates


def find_cubism_core():
    return next((p for p in cubism_core_paths() if p.is_file()), None)


class CubismCore:
    def __init__(self):
        dll_path = find_cubism_core()
        if dll_path is None:
            raise FileNotFoundError(
                'Cubism Core not found. Run setup_windows.bat and select your official Cubism SDK ZIP. '
                'Checked LIVE2D_CORE_PATH, the permanent user runtime directory, and the local live2d_sdk directory.'
            )
        d = C.CDLL(str(dll_path))
        p = C.c_void_p
        _pointer(d,'csmReviveMocInPlace',p,p,C.c_uint)
        _pointer(d,'csmGetSizeofModel',C.c_uint,p)
        _pointer(d,'csmInitializeModelInPlace',p,p,p,C.c_uint)
        _pointer(d,'csmUpdateModel',None,p)
        _pointer(d,'csmReadCanvasInfo',None,p,C.POINTER(Vector2),C.POINTER(Vector2),C.POINTER(C.c_float))
        _pointer(d,'csmGetDrawableCount',C.c_int,p)
        _pointer(d,'csmGetParameterCount',C.c_int,p)
        _pointer(d,'csmGetParameterIds',C.POINTER(C.c_char_p),p)
        for pname in ('csmGetParameterMinimumValues', 'csmGetParameterMaximumValues',
                      'csmGetParameterDefaultValues', 'csmGetParameterValues'):
            _pointer(d,pname,C.POINTER(C.c_float),p)
        for name,ctype in [('csmGetDrawableTextureIndices',C.c_int),('csmGetRenderOrders',C.c_int),
                           ('csmGetDrawableVertexCounts',C.c_int),('csmGetDrawableIndexCounts',C.c_int)]:
            _pointer(d,name,C.POINTER(ctype),p)
        _pointer(d,'csmGetDrawableOpacities',C.POINTER(C.c_float),p)
        _pointer(d,'csmGetDrawableVertexPositions',C.POINTER(C.POINTER(Vector2)),p)
        _pointer(d,'csmGetDrawableVertexUvs',C.POINTER(C.POINTER(Vector2)),p)
        _pointer(d,'csmGetDrawableIndices',C.POINTER(C.POINTER(C.c_ushort)),p)
        _pointer(d,'csmGetDrawableMaskCounts',C.POINTER(C.c_int),p)
        _pointer(d,'csmGetDrawableMasks',C.POINTER(C.POINTER(C.c_int)),p)
        _pointer(d,'csmGetDrawableConstantFlags',C.POINTER(C.c_ubyte),p)
        self.dll=d

    def load(self, path):
        return CubismMeshModel(path,self.dll)


_model_load_lock = threading.Lock()
_core = None
_models = {}
_pose_images = {}
_errors = set()
_last_error = {}
# CPU Cubism triangle rasterization is expensive. Build pose at bounded raster
# resolution, then composite the cached RGBA image at project dimensions.
# Moving the layer still updates on every regular canvas frame.
MAX_LIVE2D_RASTER_EDGE = 640
MIN_LIVE2D_POSE_INTERVAL = 1 / 30


def live2d_error(path):
    return _last_error.get(str(path), '')

def _draw_live2d_sync(painter, layer, canvas_size):
    """Paint a cached static mesh pose; reuse across live preview and recording."""
    global _core
    path = layer.get('path', '')
    try:
        with _model_load_lock:
            if _core is None:
                _core = CubismCore()
            if path not in _models:
                _models[path] = _core.load(path)
        model = _models[path]
        w, h = canvas_size
        pose = dict(layer.get('_pose_override', {}))
        if layer.get('tracking', False):
            from face_tracking import current_tracking
            pose = current_tracking()
        if layer.get('idle_enabled', False):
            # The Qt CPU mesh renderer is costly; cap dynamic geometry rebuilds.
            import time
            now = time.monotonic()
            if now >= model._next_idle_update:
                model._idle_pose = animation_values(layer, microphone.level(), now)
                model._next_idle_update = now + 1/15
            pose.update(getattr(model, '_idle_pose', {}))
        # Keep alpha12's static pose unchanged until tracking actually provides data.
        if not pose and model.last_pose is not None and model.parameter_defaults:
            pose = {k: model.parameter_defaults[i] for k, i in model.parameter_indices.items()
                    if k in ('ParamAngleX', 'ParamAngleY', 'ParamAngleZ', 'ParamBodyAngleX')}
        movement = layer.get('movement', {})
        if movement.get('enabled', False) and movement.get('model_mode', 'head') != 'models':
            # A local override is intentional: keyboard movement wins over head Y tracking.
            strength = float(movement.get('head_strength', 65)) / 100.0
            pose['ParamAngleY'] = float(layer.get('_movement_turn', 0)) * 30.0 * strength
        # Changes in animation parameters may require costly mesh reconstruction.
        # Allow normal translation/scale to render at full canvas FPS while
        # limiting geometry regeneration on the UI thread.
        import time
        now_pose = time.monotonic()
        next_pose = getattr(model, '_next_mesh_update', 0.0)
        ready = now_pose >= next_pose or model.last_pose is None
        changed = model.set_tracking(pose) if ready else False
        if changed:
            model._next_mesh_update = now_pose + MIN_LIVE2D_POSE_INTERVAL
        if changed:
            for old_key in list(_pose_images):
                if old_key[0] == path:
                    del _pose_images[old_key]
        key = (path, int(w), int(h))
        if key not in _pose_images:
            raster_scale = min(1.0, MAX_LIVE2D_RASTER_EDGE / max(w, h))
            raster_w = max(1, round(w * raster_scale))
            raster_h = max(1, round(h * raster_scale))
            image = QImage(raster_w, raster_h, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            p = QPainter(image)
            try:
                import time
                draw_start = time.perf_counter()
                model.draw(p, raster_w, raster_h)
                draw_elapsed = time.perf_counter() - draw_start
                # Adaptive safety valve: if drawing this model is slow, spend
                # less time updating its pose so key/mouse events stay usable.
                model._next_mesh_update = max(
                    getattr(model, '_next_mesh_update', 0.0),
                    time.monotonic()
                )
            finally:
                p.end()
            _pose_images[key] = image
            # Keep memory bounded when switching projects or canvas resolutions.
            while len(_pose_images) > 5:
                _pose_images.pop(next(iter(_pose_images)))
        _last_error.pop(str(path), None)
        painter.save()
        try:
            painter.translate(w * float(layer.get('x', 0)) / 100, h * float(layer.get('y', 0)) / 100)
            scale = max(0.01, float(layer.get('scale', 1)))
            if movement.get('enabled', False):
                far_y = float(movement.get('far_y', -35))
                near_y = float(movement.get('near_y', 35))
                y = float(layer.get('y', 0))
                fraction = max(0., min(1., (y - far_y) / max(1., near_y - far_y)))
                far_scale = float(movement.get('far_scale', 40)) / 100.
                near_scale = float(movement.get('near_scale', 100)) / 100.
                scale *= far_scale + (near_scale - far_scale) * fraction
            painter.translate(w / 2, h / 2)
            painter.scale(scale, scale)
            painter.translate(-w / 2, -h / 2)
            painter.setOpacity(max(0, min(1, float(layer.get('opacity', 100)) / 100)))
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(QRectF(0, 0, w, h), _pose_images[key])
        finally:
            painter.restore()
    except Exception as exc:
        _last_error[str(path)] = str(exc)
        if path not in _errors:
            print(f'Live2D layer unavailable ({path}): {exc}', flush=True)
            _errors.add(path)



def visible_bounds(image):
    """Crop transparent margins for painting only; model coordinates stay unchanged."""
    import numpy as np
    pixels=np.frombuffer(image.constBits(),dtype=np.uint8).reshape(image.height(),image.bytesPerLine())
    alpha=pixels[:,:image.width()*4].reshape(image.height(),image.width(),4)[:,:,3]
    rows=np.flatnonzero(np.any(alpha,axis=1));cols=np.flatnonzero(np.any(alpha,axis=0))
    if not len(rows) or not len(cols):return (0,0,0,0)
    left=max(0,int(cols[0])-1);top=max(0,int(rows[0])-1)
    right=min(image.width(),int(cols[-1])+2);bottom=min(image.height(),int(rows[-1])+2)
    return (left,top,right-left,bottom-top)


class PoseWorker:
    """One model owner; coalesce poses instead of queuing obsolete frames.

    Workers paint QImage only. Qt widgets and QPixmap remain on the GUI thread.
    """
    def __init__(self):
        self.condition = threading.Condition()
        self.pending = None
        self.latest = QImage()
        self.running = True
        self.render_ms = 0.
        self.updated = 0.
        self.thread = threading.Thread(target=self.run, name='Cubism pose renderer', daemon=True)
        self.thread.start()

    def request(self, layer, size):
        with self.condition:
            self.pending = (layer, size)
            self.condition.notify()
            return self.latest, self.render_ms, self.updated

    def run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.pending is not None or not self.running)
                if not self.running:
                    return
                layer, size = self.pending
                self.pending = None
            started = time.perf_counter()
            image = QImage(*size, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            try:
                _draw_live2d_sync(painter, layer, size)
            finally:
                painter.end()
            image._visible_bounds=visible_bounds(image)
            with self.condition:
                self.latest = image
                self.render_ms = (time.perf_counter() - started) * 1000
                self.updated = time.monotonic()
            # Bound CPU work while always taking the newest available pose.
            interval=max(1/30,min(1/15,self.render_ms/1000*3))
            time.sleep(max(0.,interval-(time.perf_counter()-started)))

    def retire(self):
        with self.condition:
            self.running = False
            self.condition.notify()
    def close(self):
        self.retire()
        self.thread.join(timeout=5)


_pose_workers = {}
_retired_workers = []

def close_render_workers():
    for worker in list(_pose_workers.values()) + _retired_workers:
        worker.close()
    _pose_workers.clear()
    _retired_workers.clear()


def pose_diagnostics():
    return {path: {'pose_render_ms': round(worker.render_ms, 2),
            'pose_age_ms': round((time.monotonic() - worker.updated) * 1000, 2) if worker.updated else None}
            for path, worker in _pose_workers.items()}


def draw_live2d(painter, layer, canvas_size):
    path = layer.get('_movement_model_path') or layer.get('path', '')
    if not path:
        return
    # No DLL load, pose rebuild or mesh rasterization on Qt's event thread.
    worker = _pose_workers.get(path)
    if worker is None:
        if len(_pose_workers) >= 8:
            oldest = next(iter(_pose_workers))
            retired = _pose_workers.pop(oldest)
            retired.retire()
            _retired_workers.append(retired)
        worker = _pose_workers[path] = PoseWorker()
    movement = layer.get('movement', {})
    warm_paths = set((movement.get('direction_models') or {}).values()) if movement.get('model_mode', 'head') != 'head' else set()
    warm_paths.add(layer.get('path', ''))
    warm_size = (max(1, round(canvas_size[0] * min(1., MAX_LIVE2D_RASTER_EDGE/max(canvas_size)))),
                 max(1, round(canvas_size[1] * min(1., MAX_LIVE2D_RASTER_EDGE/max(canvas_size)))))
    warm_key = (tuple(sorted(warm_paths)), warm_size)
    if layer.get('_movement_warm_key') != warm_key:
        layer['_movement_warm_key'] = warm_key
        for warm_path in sorted(warm_paths):
            if warm_path and warm_path not in _pose_workers and len(_pose_workers) < 8:
                warm_worker = _pose_workers[warm_path] = PoseWorker()
                warm_worker.request(dict(type='live2d', path=warm_path, _pose_override={}), warm_size)
    pose = {}
    if layer.get('tracking', False):
        from face_tracking import current_tracking
        pose.update(current_tracking())
    if layer.get('idle_enabled', False):
        pose.update(animation_values(layer, microphone.level(), time.monotonic()))
    if layer.get('movement', {}).get('enabled', False) and layer['movement'].get('model_mode', 'head') != 'models':
        pose['ParamAngleY'] = float(layer.get('_movement_turn', 0)) * 30 * float(layer['movement'].get('head_strength', 65)) / 100
    neutral = dict(type='live2d', path=path, x=0, y=0, scale=1, opacity=100, _pose_override=pose)
    w, h = canvas_size
    ratio = min(1., MAX_LIVE2D_RASTER_EDGE / max(w, h))
    image, _, _ = worker.request(neutral, (max(1, round(w * ratio)), max(1, round(h * ratio))))
    if image.isNull() or live2d_error(path):
        fallback = _pose_workers.get(layer.get('_movement_ready_model', layer.get('path', '')))
        if fallback is not None and fallback is not worker:
            image, _, _ = fallback.request(dict(type='live2d', path=layer.get('_movement_ready_model', layer.get('path', '')), _pose_override=pose), warm_size)
        if image.isNull():
            return
    else:
        layer['_movement_ready_model'] = path
    movement = layer.get('movement', {})
    painter.save()
    try:
        painter.translate(w * float(layer.get('x', 0)) / 100, h * float(layer.get('y', 0)) / 100)
        scale = max(0.01, float(layer.get('scale', 1)))
        if movement.get('enabled', False):
            far_y = float(movement.get('far_y', -35))
            near_y = float(movement.get('near_y', 35))
            y = float(layer.get('y', 0))
            fraction = max(0., min(1., (y - far_y) / max(1., near_y - far_y)))
            far_scale = float(movement.get('far_scale', 40)) / 100.
            near_scale = float(movement.get('near_scale', 100)) / 100.
            scale *= far_scale + (near_scale - far_scale) * fraction
        painter.translate(w / 2, h / 2)
        painter.scale(scale, scale)
        painter.translate(-w / 2, -h / 2)
        painter.setOpacity(max(0, min(1, float(layer.get('opacity', 100)) / 100)))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        bounds=getattr(image,'_visible_bounds',(0,0,image.width(),image.height()))
        x,y,bw,bh=bounds
        if bw and bh:
            painter.drawImage(QRectF(x*w/image.width(),y*h/image.height(),bw*w/image.width(),bh*h/image.height()),image,QRectF(x,y,bw,bh))
    finally:
        painter.restore()
