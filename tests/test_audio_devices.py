from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from core import audio_devices


class _InputStream:
    def __init__(self, **kwargs):
        self.callback = kwargs["callback"]
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self):
        self.started = True
        # A callback stream, exactly as main.py opens the microphone.
        self.callback(None, 6000, None, None)

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


class _OutputStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.closed = False
        self.written = None

    def start(self):
        self.started = True

    def write(self, data):
        self.written = data

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


class AudioDeviceRegressionTests(unittest.TestCase):
    def setUp(self):
        self.old_cache = audio_devices._cache
        self.old_probe_results = dict(audio_devices._probe_results)
        self.old_chosen_api = dict(audio_devices._chosen_api)
        self.old_rates = dict(audio_devices._RATES)
        audio_devices._cache = None
        audio_devices._probe_results.clear()
        audio_devices._chosen_api.update({"input": None, "output": None})

    def tearDown(self):
        audio_devices._cache = self.old_cache
        audio_devices._probe_results.clear()
        audio_devices._probe_results.update(self.old_probe_results)
        audio_devices._chosen_api.clear()
        audio_devices._chosen_api.update(self.old_chosen_api)
        audio_devices._RATES.clear()
        audio_devices._RATES.update(self.old_rates)

    def test_system_default_is_resolved_without_enumerating_devices(self):
        self.assertIsNone(audio_devices.resolve("", "input"))
        self.assertIsNone(audio_devices.resolve(audio_devices.DEFAULT_LABEL, "output"))

    def test_device_list_is_cached_but_callers_receive_a_copy(self):
        expected = {"input": ["Built-in Microphone"], "output": ["Speakers"]}
        with patch.object(audio_devices, "_query", return_value=expected) as query:
            first = audio_devices.list_devices("input")
            first.append("not a real device")
            second = audio_devices.list_devices("input")

        self.assertEqual(second, ["Built-in Microphone"])
        query.assert_called_once_with()

    def test_configure_updates_rates_and_invalidates_cached_devices(self):
        audio_devices._cache = {"input": ["old mic"], "output": ["old speakers"]}

        audio_devices.configure(22050, 44100)

        self.assertEqual(audio_devices._RATES, {"input": 22050, "output": 44100})
        self.assertIsNone(audio_devices._cache)

    def test_input_transport_probe_uses_the_callback_stream_used_by_the_app(self):
        streams = []

        def build_stream(**kwargs):
            stream = _InputStream(**kwargs)
            streams.append(stream)
            return stream

        fake_sd = types.SimpleNamespace(InputStream=build_stream)
        with (
            patch.dict(sys.modules, {"sounddevice": fake_sd}),
            patch.object(audio_devices.time, "sleep", return_value=None),
        ):
            ok = audio_devices._transport_works(
                4, "input", ("directsound", "input"))

        self.assertTrue(ok)
        self.assertEqual(len(streams), 1)
        self.assertTrue(streams[0].started)
        self.assertTrue(streams[0].stopped)
        self.assertTrue(streams[0].closed)

    def test_output_transport_probe_uses_raw_stream_and_checks_clocked_write(self):
        streams = []

        def build_stream(**kwargs):
            stream = _OutputStream(**kwargs)
            streams.append(stream)
            return stream

        fake_sd = types.SimpleNamespace(RawOutputStream=build_stream)
        with (
            patch.dict(sys.modules, {"sounddevice": fake_sd}),
            patch.object(audio_devices.time, "monotonic", side_effect=[1.0, 1.5]),
        ):
            ok = audio_devices._transport_works(
                8, "output", ("mme", "output"))

        self.assertTrue(ok)
        self.assertEqual(len(streams), 1)
        self.assertEqual(streams[0].kwargs["device"], 8)
        self.assertTrue(streams[0].started)
        self.assertEqual(len(streams[0].written), int(
            audio_devices._RATES["output"] * audio_devices._PROBE_SECONDS["output"]) * 2)
        self.assertTrue(streams[0].stopped)
        self.assertTrue(streams[0].closed)

    def test_failed_probe_result_is_cached_for_the_host_api(self):
        calls = []
        def broken_stream(**kwargs):
            calls.append(kwargs)
            raise RuntimeError("device disappeared")

        fake_sd = types.SimpleNamespace(InputStream=broken_stream)
        key = ("wasapi", "input")
        with patch.dict(sys.modules, {"sounddevice": fake_sd}):
            self.assertFalse(audio_devices._transport_works(3, "input", key))
            self.assertFalse(audio_devices._transport_works(3, "input", key))

        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
