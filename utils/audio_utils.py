import wave, os, numpy as np, soundfile as sf
from pydub import AudioSegment

print("🎯 audio_utils path:", __file__)

def light_analyze(wav_path):
    """
    ①〜③ の超軽量解析だけを行い、
    (score:int, is_fallback:bool) を返す
    """
    # WAV読み込み：soundfile → pydub フォールバック
    try:
        y, sr = sf.read(wav_path, dtype='float32')
    except Exception:
        audio = AudioSegment.from_file(wav_path)
        arr = np.array(audio.get_array_of_samples()).astype(np.float32)
        sr = audio.frame_rate
        if audio.channels == 2:
            arr = arr.reshape((-1, 2)).mean(axis=1)
        y = arr

    # 無音・短時間チェック
    duration = len(y) / sr
    abs_y = np.abs(y)
    if duration < 1.5 or np.mean(abs_y < 0.01) > 0.95:
        return 50, True

    # ① 声量変動（振幅STD）
    vol_std = float(np.std(abs_y))

    # ② 有声音率（単純閾値）
    voiced_ratio = float((abs_y > 0.02).sum()) / len(abs_y)

    # ③ ゼロ交差率（高速計算）
    zero_crossings = ((y[:-1] * y[1:]) < 0).sum()
    zcr = float(zero_crossings) / len(y)

    # 0–100 スケーリング
    vol_s   = np.clip(vol_std   * 1500, 0, 100)
    voice_s = np.clip(voiced_ratio * 120, 0, 100)
    zcr_s   = np.clip(zcr          * 100, 0, 100)

    # 重みづけ
    raw = vol_s * 0.4 + voice_s * 0.4 + zcr_s * 0.2
    score = round(np.clip(raw, 30, 95))
    return score, False

# ────────── ここからスコア解析 ──────────
def analyze_stress_from_wav(wav_path):
    """
    return (score:int, is_fallback:bool)
    30–95 点でスコアリング
    """
    try:
        y, sr = sf.read(wav_path, dtype='float32')
        if y.ndim == 2:
            y = y.mean(axis=1)
        if y.size == 0:
            raise ValueError("empty")

        duration = y.size / sr
        abs_y    = np.abs(y)
        silence_ratio = np.mean(abs_y < 0.01)

        if duration < 1.5 or silence_ratio > 0.95:
            return 50, True

        # ---------- ① 声量変動 ----------
        volume_std = np.std(abs_y)

        # ---------- ② 精密 Voiced 率 ----------
        intervals = librosa.effects.split(y, top_db=40)
        voiced_dur = sum(e - s for s, e in intervals) / sr
        voiced_ratio = voiced_dur / duration

        # ---------- ③ ゼロ交差率 ----------
        zcr = librosa.feature.zero_crossing_rate(y, frame_length=2048, hop_length=512).mean()

        # ---------- ④ ピッチ標準偏差 ----------
        pitches, mags = librosa.piptrack(y=y, sr=sr)
        p = pitches[mags > np.median(mags)]
        pitch_std = np.std(p) if p.size else 0.0

        # ---------- ⑤ テンポ（音節/秒近似） ----------
        onset_frames = librosa.onset.onset_detect(y=y, sr=sr, units='frames')
        onset_times  = librosa.frames_to_time(onset_frames, sr=sr)
        if len(onset_times) > 1:
            tempo_val = len(onset_times) / (onset_times[-1] - onset_times[0])
        else:
            tempo_val = 0.0

        # ============ スケーリング 0-100 ============
        vol_scaled   = np.clip(volume_std   * 1500, 0, 100)
        voice_scaled = np.clip(voiced_ratio * 120, 0, 100)
        zcr_scaled   = np.clip(zcr          * 5000, 0, 100)
        pitch_scaled = np.clip(pitch_std    * 0.05, 0, 100)
        tempo_scaled = 100 - np.clip(abs(tempo_val - 5) * 20, 0, 100)  # 5 音節/秒を中心に

        # ============ 重みづけ ============
        score_raw = (
              vol_scaled   * 0.25
            + voice_scaled * 0.25
            + zcr_scaled   * 0.15
            + pitch_scaled * 0.15
            + tempo_scaled * 0.20
        )
        score = round(np.clip(score_raw, 30, 95))   # 上限は 95 のまま
        return score, False

    except Exception as e:
        print("❌ analyze error:", e)
        return 50, True
