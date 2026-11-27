import os
import sys
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.engine.llm_engine import LocalLLMEngine


class TestCrossFeatureCoexistenceAndLowVRAM(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="vox_cross_feature_test_")
        self.config = AppConfig()
        self.config.storage_dir = str(Path(self.temp_dir) / "data")
        self.config.recordings_dir.mkdir(parents=True, exist_ok=True)
        self.config.disable_model_preload = True
        self.config.progressive_transcription = False
        self.controller = AppController(self.config)

    def tearDown(self):
        try:
            self.controller.shutdown()
        except Exception:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_screen_recording_rejected_during_active_meeting(self):
        # Put meeting state in recording
        self.controller._meeting_state = "recording"
        ok, msg = self.controller.start_screen_recording(title="Collision Screen Rec")
        self.assertFalse(ok)
        self.assertIn("corporate meeting recording is currently active", msg.lower())
        self.assertEqual(self.controller.screen_recording_state, "idle")

    def test_meeting_recording_rejected_during_active_screen_rec(self):
        # Put screen rec state in recording
        self.controller._screen_rec_state = "recording"
        ok, msg = self.controller.start_meeting_recording(title="Collision Meeting Rec")
        self.assertFalse(ok)
        self.assertIn("screen recording is currently active", msg.lower())
        self.assertEqual(self.controller.meeting_state, "idle")

    def test_voice_typing_rejected_during_active_meeting(self):
        self.controller._meeting_state = "recording"
        notifications = []
        self.controller.notification_requested.connect(lambda title, msg: notifications.append((title, msg)))

        # Push to talk
        res_ptt = self.controller.start_dictation()
        self.assertFalse(res_ptt)
        self.assertTrue(any("Voice Typing Unavailable" in n[0] for n in notifications))

        # Hands free
        res_hf = self.controller.start_hands_free_typing()
        self.assertFalse(res_hf)
        self.assertTrue(any("corporate meeting is being recorded" in n[1].lower() for n in notifications))

    def test_voice_typing_rejected_during_active_screen_rec(self):
        self.controller._screen_rec_state = "recording"
        notifications = []
        self.controller.notification_requested.connect(lambda title, msg: notifications.append((title, msg)))

        res_ptt = self.controller.start_dictation()
        self.assertFalse(res_ptt)
        self.assertTrue(any("screen recording is active" in n[1].lower() for n in notifications))

        res_hf = self.controller.start_hands_free_typing()
        self.assertFalse(res_hf)
        self.assertTrue(any("screen recording is active" in n[1].lower() for n in notifications))

    def test_call_detector_poll_suppressed_during_screen_recording(self):
        self.controller._screen_rec_state = "recording"
        mock_detector = MagicMock()
        mock_detector.poll.return_value = "started"
        self.controller.call_detector = mock_detector

        # Should return immediately and not even call poll()
        self.controller._poll_call_detector()
        mock_detector.poll.assert_not_called()

    def test_llm_engine_vram_aware_cpu_selection_under_low_memory(self):
        dummy_model_dir = Path(self.temp_dir) / "dummy_model"
        dummy_model_dir.mkdir(parents=True, exist_ok=True)
        model_file = dummy_model_dir / LocalLLMEngine.DEFAULT_MODEL
        model_file.write_bytes(b"GGUF" + b"\x00" * 1024)

        engine = LocalLLMEngine(model_dir=model_file, device="auto")
        variants_called = []

        def _fake_launch(bin_path, variant):
            variants_called.append(variant)
            engine._server._active_device = "cpu" if variant == "cpu" else "vulkan_gpu"
            fake_proc = MagicMock()
            fake_proc.poll.return_value = None
            engine._server._proc = fake_proc
            return True, f"Started {variant}"

        # Mock nvidia-smi returning 800 MB free (which is < 1600 MB VRAM floor)
        try:
            with patch.object(engine._server, "_get_free_vram_mb", return_value=800), \
                 patch.object(engine._server, "_get_memory_mb", return_value=(16384, 8192)), \
                 patch("voxlocal.engine.llama_server_engine.resolve_llama_server_bin", return_value=(Path("fake_llama.exe"), "cpu")), \
                 patch.object(engine._server, "_launch_variant", side_effect=_fake_launch):
                loaded = engine._server.ensure_started()
                self.assertTrue(loaded)
                self.assertEqual(variants_called, ["cpu"])
                self.assertEqual(engine._server.active_device, "cpu")
                self.assertIn("1600 MB", engine._server.fallback_reason)
        finally:
            engine._server._proc = None

    def test_llm_engine_cuda_oom_automatic_fallback(self):
        dummy_model_dir = Path(self.temp_dir) / "dummy_model"
        dummy_model_dir.mkdir(parents=True, exist_ok=True)
        model_file = dummy_model_dir / LocalLLMEngine.DEFAULT_MODEL
        model_file.write_bytes(b"GGUF" + b"\x00" * 1024)

        engine = LocalLLMEngine(model_dir=model_file, device="auto")

        def _fake_resolve(prefer_gpu: bool = True):
            return (Path("fake_vulkan.exe"), "vulkan") if prefer_gpu else (Path("fake_cpu.exe"), "cpu")

        def _fake_launch(bin_path, variant):
            if variant == "vulkan":
                return False, "Vulkan out of memory"
            engine._server._active_device = "cpu"
            fake_proc = MagicMock()
            fake_proc.poll.return_value = None
            engine._server._proc = fake_proc
            return True, "Started cpu"

        try:
            with patch.object(engine._server, "_get_free_vram_mb", return_value=3200), \
                 patch.object(engine._server, "_get_memory_mb", return_value=(16384, 8192)), \
                 patch("voxlocal.engine.llama_server_engine.resolve_llama_server_bin", side_effect=_fake_resolve), \
                 patch.object(engine._server, "_launch_variant", side_effect=_fake_launch), \
                 patch.object(engine._server, "chat", return_value="Recovered on CPU"):
                started = engine._server.ensure_started()
                self.assertTrue(started)
                self.assertEqual(engine._server.active_device, "cpu")
                self.assertIn("Vulkan GPU failed", engine._server.fallback_reason)
                out = engine.generate("Summarize meeting")
                self.assertEqual(out, "Recovered on CPU")
        finally:
            engine._server._proc = None


if __name__ == "__main__":
    unittest.main()
