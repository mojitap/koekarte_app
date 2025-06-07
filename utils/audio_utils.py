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
        # 読み込み（モノラル変換）
        audio, sr = sf.read(wav_path)
        if len(audio.shape) > 1:
            audio = np.mean(audio, axis=1)

        duration = len(audio) / sr
        if duration < 1.5:
            print("⏱ 録音が短すぎ → スコア固定（50）")
            return 50, True  # ← True を追加

        # 無音率計算
        abs_audio = np.abs(audio)
        silence_thresh = 0.01
        silence_ratio = np.sum(abs_audio < silence_thresh) / len(abs_audio)
        if silence_ratio > 0.95:
            print("🔇 無音が多すぎる → スコア固定（50）")
            return 50, True  # ← True を追加

        # 振幅の揺らぎ
        volume_std = np.std(abs_audio)
        volume_std_scaled = np.clip(volume_std * 1500, 0, 100)

        # 有声音（声が出てる割合）
        voiced_ratio = 1 - silence_ratio
        voiced_scaled = np.clip(voiced_ratio * 100, 0, 100)

        # スコア計算（声の大小に依存しない）
        score = (
            volume_std_scaled * 0.6 +  # 抑揚・変化
            voiced_scaled * 0.4        # 声が出ているか
        )

        score = round(np.clip(score, 30, 95))
        print(f"📊 スコア: {score}（voiced: {voiced_ratio:.2f}, std: {volume_std:.4f}）")
        return score, False  # ← 正常時は False を返す

    except Exception as e:
        print("❌ 分析エラー:", e)
        return 50, True  # ← エラー時も fallback とする
