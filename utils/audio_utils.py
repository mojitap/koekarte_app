import wave, os, numpy as np, soundfile as sf, librosa
from pydub import AudioSegment

print("🎯 audio_utils path:", __file__)

def light_analyze(wav_path):
    """
    ①〜④ の軽量解析だけを行い、
    (score:int, is_fallback:bool) を返す
    """
    # WAV 読み込み
    try:
        y, sr = sf.read(wav_path, dtype='float32')
    except Exception:
        audio = AudioSegment.from_wav(wav_path)
        y = np.array(audio.get_array_of_samples()).astype(np.float32)
        sr = audio.frame_rate

    if y.ndim == 2:
        y = y.mean(axis=1)

    duration = len(y) / sr
    abs_y = np.abs(y)
    if duration < 1.5 or np.mean(abs_y < 0.01) > 0.95:
        return 50, True

    # ① 声量変動
    volume_std = float(np.std(abs_y))

    # ② 精密 Voiced 率
    intervals = librosa.effects.split(y, top_db=40)
    voiced_dur = sum(e - s for s, e in intervals) / sr
    voiced_ratio = voiced_dur / duration

    # ③ ゼロ交差率
    zcr = float(librosa.feature.zero_crossing_rate(
        y, frame_length=2048, hop_length=512).mean())

    # ④ ピッチ標準偏差
    pitches, mags = librosa.piptrack(y=y, sr=sr)
    p = pitches[mags > np.median(mags)]
    pitch_std = float(np.std(p)) if p.size else 0.0

    # スケーリング
    vol_scaled   = np.clip(volume_std   * 1500, 0, 100)
    voice_scaled = np.clip(voiced_ratio * 120, 0, 100)
    zcr_scaled   = np.clip(zcr          * 5000, 0, 100)
    pitch_scaled = np.clip(pitch_std    * 0.05, 0, 100)

    # 重みづけ（例）
    raw = (
        vol_scaled * 0.3 +
        voice_scaled * 0.3 +
        zcr_scaled * 0.2 +
        pitch_scaled * 0.2
    )
    score = round(np.clip(raw, 30, 95))
    return score, False
    
# ────────── WAV 変換系（変更なし）──────────
def convert_webm_to_wav(input_path, output_path):
    audio = AudioSegment.from_file(input_path, format="webm")
    audio.export(output_path, format="wav")

def convert_m4a_to_wav(input_path, output_path):
    import subprocess
    subprocess.run([
        'ffmpeg', '-y', '-i', input_path,
        '-acodec', 'pcm_s16le', '-ac', '1', '-ar', '44100',
        '-f', 'wav', output_path
    ], check=True)
    print("✅ ffmpeg変換成功")

def normalize_volume(input_path, output_path, target_dBFS=-5.0):
    audio = AudioSegment.from_file(input_path)
    diff = target_dBFS - audio.dBFS
    audio.apply_gain(diff).export(
        output_path, format="wav",
        parameters=['-acodec','pcm_s16le','-ar','44100','-ac','1']
    )

def is_valid_wav(wav_path, min_duration_sec=1.5):
    try:
        with wave.open(wav_path) as wf:
            return wf.getnframes() / wf.getframerate() >= min_duration_sec
    except Exception as e:
        print("❌ WAV検証エラー:", e); return False

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
