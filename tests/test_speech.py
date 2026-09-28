import os
import types
import unittest
from unittest.mock import Mock, patch

from jarvis.speech import Speech


class SpeechTests(unittest.TestCase):
    def test_disabled_and_oversized_audio_are_rejected(self):
        with patch.dict(os.environ, {'JARVIS_VOICE':'0'}):
            with self.assertRaisesRegex(ValueError, 'Enable local voice'):
                Speech().transcribe(b'audio')
        with patch.dict(os.environ, {'JARVIS_VOICE':'1'}):
            speech = Speech()
            for audio in [b'', b'x'*(8*1024*1024+1)]:
                with self.assertRaises(ValueError):
                    speech.transcribe(audio)

    def test_local_model_is_lazy_and_audio_does_not_create_files(self):
        model = Mock()
        model.transcribe.return_value = ([types.SimpleNamespace(text=' open Safari ')], types.SimpleNamespace(duration=2))
        whisper = Mock(return_value=model)
        module = types.SimpleNamespace(WhisperModel=whisper)
        with patch.dict(os.environ, {'JARVIS_VOICE':'1'}), patch.dict('sys.modules', {'faster_whisper':module}):
            speech = Speech()
            whisper.assert_not_called()
            self.assertEqual(speech.transcribe(b'audio'), 'open Safari')
            self.assertEqual(speech.transcribe(b'audio'), 'open Safari')
            self.assertEqual(whisper.call_count, 1)
            self.assertEqual(whisper.call_args.kwargs, {'device':'cpu', 'compute_type':'int8'})
            self.assertEqual(model.transcribe.call_args.args[0].getvalue(), b'audio')

    def test_no_speech_and_long_recording_do_not_make_requests(self):
        model = Mock()
        with patch.dict(os.environ, {'JARVIS_VOICE':'1'}):
            speech = Speech()
            speech.model = model
            with patch.dict('sys.modules', {'faster_whisper':types.SimpleNamespace(WhisperModel=Mock())}):
                model.transcribe.return_value = ([], types.SimpleNamespace(duration=1))
                with self.assertRaisesRegex(ValueError, 'No speech detected'):
                    speech.transcribe(b'audio')
                model.transcribe.return_value = ([], types.SimpleNamespace(duration=60))
                with self.assertRaises(ValueError):
                    speech.transcribe(b'audio')

    def test_missing_extra_has_installation_guidance(self):
        with patch.dict(os.environ, {'JARVIS_VOICE':'1'}), patch.dict('sys.modules', {'faster_whisper':None}):
            with self.assertRaisesRegex(ValueError, 'Install local speech'):
                Speech().transcribe(b'audio')
