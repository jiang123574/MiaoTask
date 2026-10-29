import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.core.device import screen


class ResolveOCRBackendTests(unittest.TestCase):
    def test_default_auto_uses_cpu_without_cuda_ep(self):
        self.assertEqual(screen.resolve_ocr_backend(None, ['CPUExecutionProvider']),
                         ('cpu', None))
        self.assertEqual(screen.resolve_ocr_backend('auto', ()), ('cpu', None))

    def test_auto_enables_cuda_when_ep_available(self):
        self.assertEqual(
            screen.resolve_ocr_backend('auto', ['CUDAExecutionProvider', 'CPUExecutionProvider']),
            ('cuda', None))

    def test_explicit_cpu_never_enables_cuda(self):
        self.assertEqual(screen.resolve_ocr_backend('cpu', ['CUDAExecutionProvider']),
                         ('cpu', None))

    def test_forced_cuda_uses_cuda_when_available(self):
        self.assertEqual(screen.resolve_ocr_backend('cuda', ['CUDAExecutionProvider']),
                         ('cuda', None))

    def test_forced_cuda_without_ep_reports_fallback(self):
        self.assertEqual(screen.resolve_ocr_backend('cuda', ['CPUExecutionProvider']),
                         ('cpu', 'cuda-unavailable'))

    def test_unknown_preference_follows_auto(self):
        self.assertEqual(screen.resolve_ocr_backend('tpu', ['CUDAExecutionProvider']),
                         ('cuda', None))
        self.assertEqual(screen.resolve_ocr_backend('tpu', []), ('cpu', None))

    def test_params_carry_cuda_flag_only_for_cuda(self):
        cpu_params = screen._ocr_engine_params('cpu')
        self.assertNotIn('EngineConfig.onnxruntime.use_cuda', cpu_params)
        self.assertIs(screen._ocr_engine_params('cuda')[
                      'EngineConfig.onnxruntime.use_cuda'], True)


class ScopedOCRBackendTests(unittest.TestCase):
    """Engine wiring without importing real rapidocr/onnxruntime packages."""

    def _wire(self, providers, env_value):
        engine = Mock()
        engine.text_det.session.session.get_providers.return_value = providers
        rapid = SimpleNamespace(RapidOCR=Mock(return_value=engine))
        ort = SimpleNamespace(get_available_providers=Mock(return_value=list(providers)))
        env = dict(os.environ)
        if env_value is None:
            env.pop(screen.OCR_DEVICE_ENV, None)
        else:
            env[screen.OCR_DEVICE_ENV] = env_value
        screen._scoped_ocr_engine.cache_clear()
        with patch.dict(sys.modules, {'rapidocr': rapid, 'onnxruntime': ort}), \
                patch.dict(os.environ, env, clear=True):
            screen.local_ocr_engine()
        return rapid.RapidOCR.call_args.kwargs['params']

    def tearDown(self):
        screen._scoped_ocr_engine.cache_clear()

    def test_default_engine_stays_cpu(self):
        params = self._wire(['CPUExecutionProvider'], None)
        self.assertNotIn('EngineConfig.onnxruntime.use_cuda', params)

    def test_cuda_env_enables_cuda_flag(self):
        params = self._wire(['CUDAExecutionProvider', 'CPUExecutionProvider'], 'cuda')
        self.assertIs(params['EngineConfig.onnxruntime.use_cuda'], True)

    def test_auto_env_uses_cuda_when_available(self):
        params = self._wire(['CUDAExecutionProvider', 'CPUExecutionProvider'], 'auto')
        self.assertIs(params['EngineConfig.onnxruntime.use_cuda'], True)

    def test_forced_cuda_without_ep_falls_back_to_cpu_params(self):
        params = self._wire(['CPUExecutionProvider'], 'cuda')
        self.assertNotIn('EngineConfig.onnxruntime.use_cuda', params)

    def test_explicit_cpu_env_keeps_cpu_even_with_cuda_ep(self):
        params = self._wire(['CUDAExecutionProvider', 'CPUExecutionProvider'], 'cpu')
        self.assertNotIn('EngineConfig.onnxruntime.use_cuda', params)


if __name__ == '__main__':
    unittest.main()
