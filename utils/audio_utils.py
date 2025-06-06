from pydub import AudioSegment
import wave
import numpy as np
import soundfile as sf

def convert_webm_to_wav(input_path, output_path):
    """
    WebM形式の音声ファイルをWAV形式に変換
    """
    audio = AudioSegment.from_file(input_path, format="webm")
    audio.export(output_path, format="wav")

def convert_m4a_to_wav(input_path, output_path):
    """M4A → WAV に ffmpeg を使って変換"""
    import subprocess
    try:
        subprocess.run([
            'ffmpeg', '-y', '-i', input_path, output_path
        ], check=True)
    except Exception as e:
        print("❌ M4A変換失敗:", e)
        raise

def normalize_volume(input_path, output_path, target_dBFS=-5.0):
    """
    音声ファイルの音量をターゲットdBFSに正規化
    """
    audio = AudioSegment.from_file(input_path)
    change_in_dBFS = target_dBFS - audio.dBFS
    normalized_audio = audio.apply_gain(change_in_dBFS)
    normalized_audio.export(output_path, format="wav")

def is_valid_wav(wav_path, min_duration_sec=1.5):
    """
    WAVファイルが正常か＆一定時間以上あるかチェック
    """
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
        audio, sr = sf.read(wav_path)
        if len(audio.shape) > 1:
            audio = np.mean(audio, axis=1)

        duration = len(audio) / sr
        volume_max = np.max(np.abs(audio))
        volume_mean = np.mean(np.abs(audio))
        volume_std = np.std(audio)
        silence_thresh = 0.01
        silence_ratio = np.sum(np.abs(audio) < silence_thresh) / len(audio)

        if duration < 1.5:
            print("⏱ 録音が短すぎます（1.5秒未満） → スコアを50で返却")
            return 50
        if silence_ratio > 0.95:
            print("🔇 無音率が高すぎます → スコアを50で返却")
            return 50

        volume_mean_scaled = np.clip(volume_mean * 2000, 0, 100)
        volume_std_scaled = np.clip(volume_std * 1500, 0, 100)
        silence_scaled = np.clip((1 - silence_ratio) * 100, 0, 100)

        base_score = (
            volume_mean_scaled * 0.4 +
            volume_std_scaled * 0.3 +
            silence_scaled * 0.3
        )

        score = max(30, min(95, round(base_score)))
        print(f"📊 最終スコア: {score}")
        return score

    except Exception as e:
        print("❌ 簡易分析失敗:", e)
        return 50
