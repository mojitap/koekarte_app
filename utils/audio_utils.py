# utils/audio_utils.py

import soundfile as sf
import numpy as np
import gc

def compute_rms(path):
    """生データの RMS を返す（正規化前の特徴量）。"""
    data, _ = sf.read(path, dtype='float32')
    return float(np.std(data))

def light_analyze(wav_path, raw_rms=None, rms_baseline=None,
                  target_sr=16000, chunk_sec=1.0):
    """
    相対ボリューム + チャンクごと pitch/tempo でスコア算出
    raw_rms: 正規化前の RMS
    rms_baseline: ユーザーの平常時 RMS
    """
    # 1) 音量スコア（ベースライン比）
    if raw_rms is not None and rms_baseline:
        rel = (raw_rms - rms_baseline) / rms_baseline
        vol_score = np.clip(1 + rel, 0.5, 1.5) * 50
    else:
        vol_score = 50

    # 2) チャンク処理で pitch/tempo
    total_pitch = 0.0
    total_tempo = 0.0
    chunks = 0

    with sf.SoundFile(wav_path) as f:
        orig_sr = f.samplerate
        blocksize = int(orig_sr * chunk_sec)
        for block in f.blocks(blocksize=blocksize, dtype='float32'):
            # 簡易リサンプリング
            if orig_sr != target_sr:
                x_old = np.linspace(0, 1, len(block))
                x_new = np.linspace(0, 1, int(len(block)*target_sr/orig_sr))
                block = np.interp(x_new, x_old, block)

            # pitch（簡易ゼロ交差率ベース）
            zc = np.mean(np.abs(np.diff(np.sign(block))))
            total_pitch += zc * 100

            # tempo（有声音率）
            threshold = np.max(np.abs(block)) * 0.02
            voiced = np.mean(np.abs(block) > threshold)
            total_tempo += voiced * 10

            chunks += 1
            del block
            gc.collect()

    pitch_score = total_pitch/chunks if chunks else 50
    tempo_score = total_tempo/chunks if chunks else 50

    # 3) 合成＆クランプ
    raw_score = 0.3*vol_score + 0.4*pitch_score + 0.3*tempo_score
    score = int(np.clip(raw_score, 30, 95))

    print(f"⚙️ light_analyze: vol={vol_score:.1f}, pitch={pitch_score:.1f}, tempo={tempo_score:.1f} → score={score}")
    
    return score, False

# ────────── WAV 変換系 ──────────
def convert_webm_to_wav(input_path, output_path):
    import subprocess
    cmd = ['ffmpeg','-y','-i',input_path,'-acodec','pcm_s16le','-ac','1','-ar','16000','-f','wav',output_path]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return res.returncode == 0

def convert_m4a_to_wav(input_path, output_path):
    import subprocess
    cmd = ['ffmpeg','-y','-i',input_path,'-acodec','pcm_s16le','-ac','1','-ar','16000','-f','wav',output_path]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return res.returncode == 0

def normalize_volume(input_path, output_path, target_dBFS=-3.0):
    from pydub import AudioSegment
    audio = AudioSegment.from_file(input_path)
    diff = target_dBFS - audio.dBFS
    audio.apply_gain(diff).export(
        output_path, format="wav",
        parameters=['-acodec','pcm_s16le','-ar','16000','-ac','1']
    )

def is_valid_wav(wav_path, min_duration_sec=1.5):
    import wave
    try:
        with wave.open(wav_path,'rb') as wf:
            dur = wf.getnframes() / wf.getframerate()
        return dur >= min_duration_sec
    except:
        return False
