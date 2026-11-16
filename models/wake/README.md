# Custom wake-word model placement

The runtime looks for the trained model at:

`models/wake/hey_minder.onnx`

Do not place the pretrained `hey_jarvis.onnx` here under this name. Only copy
an ONNX model produced by the documented Hey MINDER training workflow after it
passes the runtime and false-activation acceptance tests.

The binary model is intentionally not committed until it has been trained and
validated. Wake-word listening remains opt-in and disabled by default.
