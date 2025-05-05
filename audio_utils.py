import wave

def is_valid_wav(path):
    try:
        with wave.open(path, 'rb') as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
            duration = frames / float(rate)
            print(f"🎧 WAV長さ: {duration:.2f}秒")
            return duration > 1.0
    except Exception as e:
        print("❌ WAVチェックエラー:", e)
        return False