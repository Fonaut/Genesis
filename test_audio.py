import sounddevice as sd
import soundfile as sf

print("🔍 Testing microphone...")
fs = 44100
duration = 3  # 3 seconds
print("🎤 Recording for 3 seconds... Speak now!")
myrecording = sd.rec(int(duration * fs), samplerate=fs, channels=1, dtype='float32')
sd.wait()
print("✅ Recording finished. Saving to test.wav...")
sf.write('test.wav', myrecording, fs)
print("🎉 Done! Play the 'test.wav' file to hear yourself.")