import wave
import os
import numpy as np
import soundfile as sf
from pydub import AudioSegment
import librosa
import shutil

print("🎯 audio_utils path:", __file__)

def light_analyze(wav_path):
    y, sr = librosa.load(wav_path, sr=None)

    volume_std = np.std(y)

    try:
        f0, voiced_flag, voiced_probs = librosa.pyin(y, fmin=80, fmax=350, sr=sr)
        pitch_std = np.nanstd(f0)
    except:
        pitch_std = None

    intervals = librosa.effects.split(y, top_db=25)
    total_voiced = sum([(end - start) for start, end in intervals])
    duration_sec = librosa.get_duration(y=y, sr=sr)
    tempo_val = total_voiced / duration_sec if duration_sec > 0 else 0

    score = int(
        (volume_std * 2500 * 0.3 if volume_std else 0) +
        (pitch_std * 0.1 * 0.4 if pitch_std else 0) +
        (tempo_val * 10 * 0.3)
    )
    score = max(30, min(score, 95))

    return score, False if pitch_std else True

# ────────── WAV 変換系 ──────────
def convert_webm_to_wav(input_path, output_path):
    import subprocess
    command = [
        'ffmpeg', '-y', '-i', input_path,
        '-acodec', 'pcm_s16le', '-ac', '1', '-ar', '44100',
        '-f', 'wav', output_path
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print(f"❌ ffmpeg変換エラー (webm): {result.stderr.decode()}")
        return False
    print("✅ ffmpeg変換成功 (webm)")
    return True

def convert_m4a_to_wav(input_path, output_path):
    import subprocess
    command = [
        'ffmpeg', '-y', '-i', input_path,
        '-acodec', 'pcm_s16le', '-ac', '1', '-ar', '44100',
        '-f', 'wav', output_path
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print(f"❌ ffmpeg変換エラー (m4a): {result.stderr.decode()}")
        return False
    print("✅ ffmpeg変換成功 (m4a)")
    return True

def normalize_volume(input_path, output_path, target_dBFS=-3.0):
    audio = AudioSegment.from_file(input_path)
    diff = target_dBFS - audio.dBFS
    normalized = audio.apply_gain(diff)
    normalized.export(
        output_path, format="wav",
        parameters=['-acodec','pcm_s16le','-ar','44100','-ac','1']
    )

def is_valid_wav(wav_path, min_duration_sec=1.5):
    try:
        with wave.open(wav_path, 'rb') as wf:
            frames = wf.getnframes()
            framerate = wf.getframerate()
            duration = frames / float(framerate)
            print(f"⏱ WAV duration = {duration:.2f}s, framerate = {framerate}")
            return duration >= min_duration_sec
    except Exception as e:
        print("❌ WAV検証エラー:", e)
        return False

# ────────── フル解析（①〜⑤）──────────
def analyze_stress_from_wav(wav_path, user_id=None):
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

        duration = len(y) / sr
        abs_y = np.abs(y)
        if duration < 1.5 or np.mean(abs_y < 0.005) > 0.98:
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
        # ⑤ テンポ
        onset = librosa.onset.onset_detect(y=y, sr=sr, units='frames')
        times = librosa.frames_to_time(onset, sr=sr)
        tempo_val = len(times)/(times[-1]-times[0]) if len(times)>1 else 0.0

        # スケーリング
        vol_scaled = np.clip(volume_std * 7000, 0, 100)
        voice_scaled = np.clip(voiced_ratio * 150, 0, 100)
        zcr_scaled = np.clip(zcr * 7000, 0, 100)
        pitch_scaled = np.clip(pitch_std * 0.3, 0, 100)
        tempo_scaled = 100 - np.clip(abs(tempo_val - 4.5) * 8, 0, 100)

        raw = (vol_scaled * 0.25 + voice_scaled * 0.2 +
               zcr_scaled * 0.2 + pitch_scaled * 0.2 + tempo_scaled * 0.15)
        score = round(np.clip(raw, 25, 97))

        user = User.query.get(user_id)
        if user and user.volume_baseline:
            if volume_std < user.volume_baseline * 0.5:
                print("⚠️ 声量が極端に低下 → 減点補正")
                score = max(25, score - 10)

        print(f"📊 スコア構成: vol={vol_scaled:.1f}, voice={voice_scaled:.1f}, "
              f"zcr={zcr_scaled:.1f}, pitch={pitch_scaled:.1f}, tempo={tempo_scaled:.1f} "
              f"→ raw={raw:.1f} → score={score}")

        # 🔻 単調すぎ・小声すぎへの罰則
        if volume_std < 0.003 or pitch_std < 0.5 or tempo_val < 0.5:
            print("⚠️ 話し方が単調または声量が極端に小さいため補正スコア適用")
            return {
                "score": 30 + np.random.randint(0, 10),
                "is_fallback": True,
                "volume_std": volume_std,
                "voiced_ratio": voiced_ratio,
                "zcr": zcr,
                "pitch_std": pitch_std,
                "tempo_val": tempo_val,
            }

        return {
            "score": score,
            "is_fallback": False,
            "volume_std": volume_std,
            "voiced_ratio": voiced_ratio,
            "zcr": zcr,
            "pitch_std": pitch_std,
            "tempo_val": tempo_val,
        }

    except Exception as e:
        print("❌ analyze error:", e)
        return {
            "score": 50,
            "is_fallback": True,
            "volume_std": None,
            "voiced_ratio": None,
            "zcr": None,
            "pitch_std": None,
            "tempo_val": None,
        }
