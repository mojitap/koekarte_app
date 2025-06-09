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
        print("📁 ファイルパス:", wav_path)
        print("🧪 ファイルサイズ:", os.path.getsize(wav_path))
        y, sr = librosa.load(wav_path, sr=44100, mono=True)

        import wave
        with wave.open(wav_path, 'rb') as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            duration_wave = frames / float(rate)
            print(f"👂 waveでの長さ: {duration_wave:.2f}秒, フレーム数: {frames}")

        y, sr = librosa.load(wav_path, sr=44100, mono=True)
        print(f"📊 librosa読み込み完了: sr={sr}, y.size={y.size}")
        
        if y.size == 0:
            raise ValueError("サンプル数が0（無音または読み込みエラー）")

        duration = librosa.get_duration(y=y, sr=sr)
        print(f"🎧 音声の長さ: {duration:.2f}秒")
        
        if duration < 1.5:
            print("⚠️ 録音が短すぎます（1.5秒未満）")
            return 50, True  # 短すぎる場合は仮スコアを返す

        abs_audio = np.abs(y)
        silence_ratio = np.mean(abs_audio < 0.01)

        if silence_ratio > 0.95:
            print("⚠️ 無音区間が多すぎます（95%以上）")
            return 50, True  # 無音が多い場合は仮スコアを返す

        volume_std = np.std(abs_audio)
        volume_std_scaled = np.clip(volume_std * 1500, 0, 100)

        voiced_ratio = 1 - silence_ratio
        voiced_scaled = np.clip(voiced_ratio * 100, 0, 100)

        score = round(np.clip(volume_std_scaled * 0.6 + voiced_scaled * 0.4, 30, 95))
        print(f"✅ 計算されたスコア: {score}")

        return score, False

    except Exception as e:
        print("❌ analyze error (librosa):", e)
        return 50, True
