from pydub import AudioSegment
import wave

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
    """
    WAV音声からストレススコアを算出する（仮実装）
    ※特徴量抽出失敗時は平均スコア50を返す
    """
    try:
        # 🔧 実際の音響特徴量抽出と分析ロジックをここに書く予定
        # 仮で例外発生の可能性をシミュレート
        raise ValueError("特徴量抽出失敗（仮）")

        # 正常に処理できた場合（今は通らない）
        # score = some_analysis_function(wav_path)
        # return score

    except Exception as e:
        print("❌ 特徴量抽出失敗（代替スコア使用）:", e)
        return 50  # fallbackスコア
