"""Prefer readable controls; visual verification skips redundant hierarchy RPCs."""
import copy
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import logging
import os
import re
import time

from backend.core.models import TextNode, bounds_of, normalize
from backend.core.device.geometry import IdentityImageProcessor, DisplayGeometry, ocr_tiles
from backend.core.context import workspace_scope
import threading

logger = logging.getLogger(__name__)

# auto：装了带 CUDA 的 onnxruntime-gpu 才启用；cpu/cuda 可显式指定。
OCR_DEVICE_ENV = 'MIAOTASK_OCR_DEVICE'
_OCR_BACKENDS = ('auto', 'cpu', 'cuda')


def resolve_ocr_backend(preference, providers):
    """Map the MIAOTASK_OCR_DEVICE preference and available EPs to a backend.

    Returns ``(backend, note)`` where backend is ``'cpu'`` or ``'cuda'`` and
    note is ``None`` or ``'cuda-unavailable'`` when a forced CUDA request has
    to fall back because CUDAExecutionProvider is missing.
    """
    requested = (preference or 'auto').strip().lower()
    if requested not in _OCR_BACKENDS:
        logger.warning('环境变量 %s=%r 无法识别，可选 %s，按 auto 处理',
                       OCR_DEVICE_ENV, preference, '/'.join(_OCR_BACKENDS))
        requested = 'auto'
    cuda_ready = 'CUDAExecutionProvider' in (providers or ())
    if requested == 'cpu':
        return 'cpu', None
    if cuda_ready:
        return 'cuda', None
    if requested == 'cuda':
        return 'cpu', 'cuda-unavailable'
    return 'cpu', None


def _ocr_engine_params(backend):
    params = {
        'EngineConfig.onnxruntime.intra_op_num_threads': min(4, os.cpu_count() or 2),
        'EngineConfig.onnxruntime.inter_op_num_threads': 1,
        'Global.log_level': 'warning',
    }
    if backend == 'cuda':
        params['EngineConfig.onnxruntime.use_cuda'] = True
    return params


def local_ocr_engine():
    return _scoped_ocr_engine(workspace_scope.get() or ('thread', threading.get_ident()))


@lru_cache(maxsize=8)
def _scoped_ocr_engine(scope):
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise RuntimeError('页面未提供可读控件，请先运行 pip install -r requirements.txt 安装本地 OCR') from exc
    providers = ()
    try:
        import onnxruntime
        providers = onnxruntime.get_available_providers()
    except ImportError:
        pass
    backend, note = resolve_ocr_backend(os.environ.get(OCR_DEVICE_ENV), providers)
    if note == 'cuda-unavailable':
        logger.warning('已通过 %s=cuda 请求 GPU 识别，但当前 onnxruntime 不含 CUDAExecutionProvider；'
                       '需安装与本机 CUDA/cuDNN 版本匹配的 onnxruntime-gpu，已回退 CPU', OCR_DEVICE_ENV)
    engine = RapidOCR(params=_ocr_engine_params(backend))
    # Models load lazily on the first frame, so the active session cannot be
    # probed here; the chosen flag plus available EPs already determine it.
    active_eps = ','.join(providers) or 'CPUExecutionProvider'
    logger.info('本地 OCR 推理后端：%s（onnxruntime 可用执行器：%s）', backend, active_eps)
    # The locked RapidOCR version exposes this recognizer protocol. If a source
    # install uses an incompatible release, retain its standard OCR path.
    try:
        from backend.core.device.ocr import ExactTextCache
        engine.text_rec = ExactTextCache(engine._load_rec_model())
    except (AttributeError, ImportError):
        logger.info('当前 OCR 版本不支持文字块缓存，使用标准识别')
    return engine


@dataclass
class Screen:
    nodes: list
    width: int
    height: int
    package: str
    source: str = "ui"
    tree: object = None
    user_id: int | None = None
    geometry: DisplayGeometry | None = None
    obscured: bool = False

    @property
    def text(self):
        return "\n".join(n.text for n in self.nodes)

    def find(self, pattern, exact=False):
        for node in self.nodes:
            if (normalize(node.text) == normalize(pattern) if exact else
                    re.search(pattern, normalize(node.text))):
                return node
        return None


class ScreenReader:
    def __init__(self, device, ocr=True, on_image=None, image_processor=None):
        self.device = device
        self.ocr_enabled = ocr
        self.on_image = on_image
        self._engine = None
        self._ocr_notice = False
        self._size = None
        self.last_image = None
        self.last_screen = None
        self._last_preview = 0
        self._ocr_cache_key = None
        self._ocr_cache_nodes = None
        self.last_observation_seconds = 0.0
        self.image_processor = image_processor or IdentityImageProcessor()

    def _flatten(self, tree, foreground_package=None):
        nodes = []
        def walk(item, inherited_package=''):
            if isinstance(item, list):
                for child in item:
                    walk(child, inherited_package)
            elif isinstance(item, dict):
                attrs = item.get("attributes", item)
                package = attrs.get("package", inherited_package)
                if (not foreground_package or not package or package == foreground_package) and not package.startswith(("com.android.systemui", "com.miui.notes", "com.google.android.inputmethod")):
                    text = attrs.get("text") or attrs.get("description") or attrs.get("content-desc") or ""
                    bounds = bounds_of(attrs.get("bounds", attrs.get("rect")))
                    if text and bounds[2] > bounds[0] and bounds[3] > bounds[1]:
                        nodes.append(TextNode(text, bounds))
                for key in ("children", "hierarchy"):
                    if isinstance(item.get(key), (list, dict)):
                        walk(item[key], package)
        walk(tree)
        return nodes

    def _ocr(self, image):
        nodes=[]
        for tile, x, y in ocr_tiles(image):
            for node in self._ocr_tile(tile):
                a,b,c,d=node.bounds
                mapped=TextNode(node.text,(a+x,b+y,c+x,d+y),node.source)
                if not any(n.text==mapped.text and abs(n.center[0]-mapped.center[0])<12 and abs(n.center[1]-mapped.center[1])<12 for n in nodes):
                    nodes.append(mapped)
        return nodes

    def _ocr_tile(self, image):
        if self._engine is None:
            self._engine = local_ocr_engine()
        import numpy as np
        prepared = self.image_processor.prepare(image)
        image = prepared.image
        # Limit image width to reduce CPU work, then map boxes back to the phone.
        scale = min(1.0, 1000 / image.width)
        resized = image.resize((round(image.width * scale), round(image.height * scale)))
        result = self._engine(np.asarray(resized), use_cls=False)
        results = zip(result.boxes, result.txts, result.scores) if result.boxes is not None else []
        nodes = []
        for box, text, confidence in results:
            if confidence < .65:
                continue
            xs, ys = zip(*box)
            bounds = prepared.to_device.bounds(tuple(round(v / scale) for v in (min(xs), min(ys), max(xs), max(ys))))
            nodes.append(TextNode(text, bounds, "ocr"))
        return nodes

    def _image_nodes(self, image):
        # Include every original RGB pixel and the dimensions. A thumbnail or
        # perceptual hash can miss a changed countdown or modal and is unsafe.
        key = (self.image_processor.cache_key, image.size, hashlib.sha256(image.tobytes()).digest())
        if key == self._ocr_cache_key:
            return copy.deepcopy(self._ocr_cache_nodes)
        nodes = self._ocr(image)
        self._ocr_cache_key = key
        self._ocr_cache_nodes = copy.deepcopy(nodes)
        return nodes

    def _capture(self):
        image = self.device.screenshot(max_age=.15).convert('RGB')
        self.last_image = image
        self._size = image.size
        if self.on_image:
            self.on_image(image)
        self._last_preview = time.monotonic()
        return image

    def enrich_regions(self, screen, regions):
        """OCR crops of this exact observed frame, keeping full-frame guards."""
        if not self.ocr_enabled or self.last_screen is not screen or self.last_image is None:
            return screen
        nodes = list(screen.nodes)
        for left, top, right, bottom in regions:
            left, top = max(0, left), max(0, top)
            right, bottom = min(screen.width, right), min(screen.height, bottom)
            if left >= right or top >= bottom:
                continue
            for node in self._ocr(self.last_image.crop((left, top, right, bottom))):
                x1, y1, x2, y2 = node.bounds
                nodes.append(TextNode(node.text, (x1+left, y1+top, x2+left, y2+top), 'ocr-region'))
        enriched = Screen(nodes, screen.width, screen.height, screen.package, 'ocr+regions', screen.tree, screen.user_id)
        self.last_screen = enriched
        return enriched

    def read(self, force_ocr=False):
        started = time.monotonic()
        direct_visual = bool(force_ocr and self.ocr_enabled)
        tree = None if direct_visual else self.device.dump_hierarchy()
        foreground = self.device.current_app()
        package = foreground.get("package", "")
        nodes = [] if direct_visual else self._flatten(tree, package)
        self._size = self.device.display_size
        meaningful = [n for n in nodes if n.bounds[3] - n.bounds[1] < (self._size or (1440, 3200))[1] * .4]
        source = "ui"
        if self.ocr_enabled and (force_ocr or len(meaningful) < 5):
            if not self._ocr_notice:
                logger.info("页面控件不可读，启用本地 OCR 识别（截图不上传）")
                self._ocr_notice = True
            image = self._capture()
            nodes = [] if max(hi for lo,hi in image.getextrema()) < 8 else self._image_nodes(image)
            source = "ocr"
            if not nodes:
                tree = self.device.dump_hierarchy()
                nodes = self._flatten(tree,package)
                source = 'ui-fallback'
        elif (self.on_image and not getattr(self.device, 'preview_managed', False)
              and time.monotonic() - self._last_preview >= 1):
            self._capture()
        if self._size is None:
            self._size = self.device.display_size
        screen = Screen(nodes, *self._size, package, source, tree, foreground.get('user_id'))
        if source == 'ui-fallback' and self.last_image is not None:
            screen.obscured = max(hi for lo,hi in self.last_image.getextrema()) < 8
        geometry = getattr(self.device,'display_geometry', None)
        if isinstance(geometry,DisplayGeometry) and (geometry.width,geometry.height)==self._size:
            screen.geometry=geometry
        self.last_screen = screen
        self.last_observation_seconds = time.monotonic() - started
        return screen
