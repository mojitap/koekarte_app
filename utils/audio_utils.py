from pydub import AudioSegment
import wave
import numpy as np
import soundfile as sf

def convert_webm_to_wav(input_path, output_path):
    audio = AudioSegment.from_file(input_path, format="webm")
    audio.export(output_path, format="wav")

def convert_m4a_to_wav(input_path, output_path):
    import subprocess
    try:
        subprocess.run([
            'ffmpeg', '-y', '-i', input_path, output_path
        ], check=True)
    except Exception as e:
        print("❌ M4A変換失敗:", e)
        raise

def normalize_volume(input_path, output_path, target_dBFS=-5.0):
    audio = AudioSegment.from_file(input_path)
    change_in_dBFS = target_dBFS - audio.dBFS
    normalized_audio = audio.apply_gain(change_in_dBFS)
    normalized_audio.export(output_path, format="wav")

def is_valid_wav(wav_path, min_duration_sec=1.5):
    try:
        with wave.open(wav_path, 'rb') as wav_file:
            frames = wav_file.getnframes()
            rate = wav_file.getframerate()
            duration = frames / float(rate)
            print(f"🎧 WAV長さ: {duration:.2f}秒")
            return duration >= min_duration_sec
    except Exception as e:
        print("❌ WAVファイル検証エラー:", e)
        return False

def analyze_stress_from_wav(wav_path):
    try:
        audio = AudioSegment.from_wav(wav_path)
        samples = np.array(audio.get_array_of_samples(), dtype=np.float32)

        # 正規化の修正箇所
        bit_depth = audio.sample_width * 8
        dtype = f'int{bit_depth}'
        max_val = float(np.iinfo(np.dtype(dtype)).max)
        samples = (samples / max_val).astype(np.float32)

        duration = len(samples) / audio.frame_rate
        if duration < 1.5:
            return 50, True

        abs_audio = np.abs(samples)
        silence_ratio = float((abs_audio < 0.01).sum()) / len(abs_audio)
        if silence_ratio > 0.95:
            return 50, True

        volume_std = float(np.std(abs_audio))
        volume_std_scaled = np.clip(volume_std * 1500, 0, 100)
        voiced_ratio = 1 - silence_ratio
        voiced_scaled = np.clip(voiced_ratio * 100, 0, 100)

        score = round(np.clip(volume_std_scaled * 0.6 + voiced_scaled * 0.4, 30, 95))
        return score, False

    except Exception as e:
        print("❌ analyze error:", e)
        return 50, True
