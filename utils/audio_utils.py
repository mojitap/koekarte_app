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
