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
        samples = np.array(audio.get_array_of_samples()).astype(np.float32)

        # 正しい正規化（bit数から推定してはいけない）
        if audio.sample_width == 2:
            samples /= np.iinfo(np.int16).max
        elif audio.sample_width == 1:
            samples = (samples - 128) / 128.0  # 8bitの場合
        else:
            raise ValueError("未対応のsample width: {}".format(audio.sample_width))

        duration = len(samples) / audio.frame_rate
        if duration < 1.5:
            print("⏱ 録音が短すぎ → スコア固定（50）")
            return 50, True

        abs_audio = np.abs(samples)
        silence_thresh = 0.01
        silence_ratio = np.sum(abs_audio < silence_thresh) / len(abs_audio)
        if silence_ratio > 0.95:
            print("🔇 無音が多すぎる → スコア固定（50）")
            return 50, True

        volume_std = np.std(abs_audio)
        volume_std_scaled = np.clip(volume_std * 1500, 0, 100)
        voiced_ratio = 1 - silence_ratio
        voiced_scaled = np.clip(voiced_ratio * 100, 0, 100)

        score = (
            volume_std_scaled * 0.6 +
            voiced_scaled * 0.4
        )
        score = round(np.clip(score, 30, 95))
        print(f"📊 スコア: {score}（voiced: {voiced_ratio:.2f}, std: {volume_std:.4f}）")
        return score, False

    except Exception as e:
        print("❌ 分析エラー:", e)
        return 50, True  # ← fallbackでも tupleで返す！
