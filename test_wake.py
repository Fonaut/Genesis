import openwakeword
from openwakeword.model import Model
import sounddevice as sd
import numpy as np

# Load the "Hey Jarvis" model
model = Model(wakeword_models=["hey_jarvis"])

print("🎤 Listening for 'Hey Jarvis'... (Ctrl+C to stop)")

# Open a microphone stream
stream = sd.InputStream(samplerate=16000, channels=1, dtype='int16', blocksize=1280)
stream.start()

try:
    while True:
        chunk, _ = stream.read(1280)
        prediction = model.predict(chunk.flatten())
        if prediction.get("hey_jarvis", 0) > 0.5:
            print("✅ Wake word detected! (Hey Jarvis)")
except KeyboardInterrupt:
    print("\nStopped.")
finally:
    stream.stop()
    stream.close()