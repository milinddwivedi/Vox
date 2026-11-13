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
        (dummy_model_dir / "model.bin").write_bytes(b"dummy")
        (dummy_model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")

        engine = LocalLLMEngine(model_dir=dummy_model_dir, device="auto")

        # Mock nvidia-smi returning 800 MB free (which is < 1400 MB)
        with patch.object(LocalLLMEngine, "_get_free_vram_mb", return_value=800):
            with patch("ctranslate2.get_cuda_device_count", return_value=1):
                with patch("ctranslate2.Generator") as mock_gen:
                    with patch("tokenizers.Tokenizer.from_file") as mock_tok:
                        loaded = engine._ensure_loaded()
                        self.assertTrue(loaded)
                        # Verify it selected cpu because VRAM was 800MB (< 1400MB threshold)
                        mock_gen.assert_called_once()
                        _, kwargs = mock_gen.call_args
                        self.assertEqual(kwargs.get("device"), "cpu")
                        self.assertEqual(engine._active_device, "cpu")

    def test_llm_engine_cuda_oom_automatic_fallback(self):
        dummy_model_dir = Path(self.temp_dir) / "dummy_model"
        dummy_model_dir.mkdir(parents=True, exist_ok=True)
        (dummy_model_dir / "model.bin").write_bytes(b"dummy")
        (dummy_model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")

        engine = LocalLLMEngine(model_dir=dummy_model_dir, device="cuda")
        engine._tokenizer = MagicMock()
        mock_tok_res = MagicMock()
        mock_tok_res.tokens = ["test"]
        engine._tokenizer.encode.return_value = mock_tok_res
        engine._tokenizer.token_to_id.return_value = 1
        engine._tokenizer.decode.return_value = "Recovered on CPU"

        # Simulate CUDA Generator throwing CUDA OOM
        mock_cuda_gen = MagicMock()
        mock_cuda_gen.generate_batch.side_effect = RuntimeError("CUDA failed with error out of memory")
        engine._generator = mock_cuda_gen
        engine._active_device = "cuda"

        # When fallback_to_cpu is called, replace with cpu generator that succeeds
        mock_cpu_gen = MagicMock()
        mock_seq = MagicMock()
        mock_seq.sequences = [["test"]]
        mock_cpu_gen.generate_batch.return_value = [mock_seq]

        with patch("ctranslate2.Generator", return_value=mock_cpu_gen):
            out = engine.generate("Summarize meeting")
            self.assertEqual(out, "Recovered on CPU")
            self.assertEqual(engine._active_device, "cpu")


if __name__ == "__main__":
    unittest.main()
