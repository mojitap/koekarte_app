import wave
import numpy as np
import soundfile as sf
import os
from scipy.io import wavfile
from pydub import AudioSegment
import librosa

def convert_webm_to_wav(input_path, output_path):
    audio = AudioSegment.from_file(input_path, format="webm")
    audio.export(output_path, format="wav")

def convert_m4a_to_wav(input_path, output_path):
    import subprocess
    try:
        subprocess.run([
            'ffmpeg', '-y',
            '-i', input_path,
            '-acodec', 'pcm_s16le',
            '-ac', '1',
            '-ar', '44100',
            '-f', 'wav',
            output_path
        ], check=True)
        print("✅ ffmpeg変換成功")
    except Exception as e:
        print("❌ M4A変換失敗:", e)
        raise

def normalize_volume(input_path, output_path, target_dBFS=-5.0):
    audio = AudioSegment.from_file(input_path)
    diff = target_dBFS - audio.dBFS
    normalized = audio.apply_gain(diff)
    normalized.export(
        output_path,
        format="wav",
        parameters=["-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1"]
    )

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
        print("📁 ファイルパス:", wav_path, "サイズ:", os.path.getsize(wav_path))
        # ---- wave モジュールでざっくり長さ確認 ----
        import wave
        with wave.open(wav_path, 'rb') as wf:
            frames, rate = wf.getnframes(), wf.getframerate()
            print(f"👂 wave 長さ: {frames/rate:.2f}s, frames={frames}")

        # ---- soundfile で読み込み（ここが核心） ----
        y, sr = sf.read(wav_path, dtype='float32')   # y.shape = (N,) or (N, ch)
        if y.ndim == 2:                              # モノラル化
            y = y.mean(axis=1)
        print(f"📊 sf.read → sr={sr}, samples={y.size}")

        if y.size == 0:
            raise ValueError("読み込めたサンプルが 0")

        # ── ここからスコア計算は以前と同じ ──
        duration = y.size / sr
        if duration < 1.5:
            return 50, True
        abs_audio = np.abs(y)
        silence_ratio = np.mean(abs_audio < 0.01)
        if silence_ratio > 0.95:
            return 50, True
        volume_std = np.std(abs_audio)
        score = round(np.clip(volume_std*1500*0.6 + (1-silence_ratio)*100*0.4, 30, 95))
        return score, False

    except Exception as e:
        print("❌ analyze error:", e)
        return 50, True
