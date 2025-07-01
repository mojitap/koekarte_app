import soundfile as sf
import numpy as np
import gc

print("🎯 audio_utils path:", __file__)

def light_analyze(wav_path, target_sr=16000, chunk_sec=1.0):
    """
    1秒単位でファイルを読み込み、volume/pitch/tempo を算出。
    メモリ消費が一定に抑えられるストリーミング処理版。
    """
    total_score = 0.0
    chunks = 0

    with sf.SoundFile(wav_path) as f:
        orig_sr = f.samplerate
        # 1秒あたりの読み込みサンプル数 (元 SR)
        blocksize = int(orig_sr * chunk_sec)

        # チャンクごとに読み込んで処理
        for block in f.blocks(blocksize=blocksize, dtype='float32'):
            # ダウンサンプル（簡易版）
            if orig_sr != target_sr:
                # numpy.interp でリサンプリング（軽量）
                x_old = np.linspace(0, 1, num=len(block))
                x_new = np.linspace(0, 1, num=int(len(block) * target_sr/orig_sr))
                block = np.interp(x_new, x_old, block)

            # 音量
            vol = np.std(block)

            # 簡易ピッチ推定（例：ゼロ交差率ベースの近似）
            zc = np.mean(np.abs(np.diff(np.sign(block))))  # ゼロ交差率指標
            pitch = zc * 100  # スケーリング係数は要調整

            # テンポ近似：有声音サンプル比率
            voiced = np.mean(np.abs(block) > 1e-4)
            tempo = voiced * 10  # スケーリング係数は要調整

            # 加重平均スコア
            score_chunk = 0.3 * (vol * 2500) + 0.4 * pitch + 0.3 * tempo
            total_score += score_chunk
            chunks += 1

            # メモリ解放
            del block
            gc.collect()

    if chunks == 0:
        return 30, True

    # 平均して 30–95 にクランプ
    avg = int(total_score / chunks)
    score = max(30, min(avg, 95))
    return score, False

# ────────── WAV 変換系 ──────────
def convert_webm_to_wav(input_path, output_path):
    import subprocess
    command = [
        'ffmpeg', '-y', '-i', input_path,
        '-acodec', 'pcm_s16le', '-ac', '1', '-ar', '16000',
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
        '-acodec', 'pcm_s16le', '-ac', '1', '-ar', '16000',
        '-f', 'wav', output_path
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print(f"❌ ffmpeg変換エラー (m4a): {result.stderr.decode()}")
        return False
    print("✅ ffmpeg変換成功 (m4a)")
    return True

def normalize_volume(input_path, output_path, target_dBFS=-3.0):
    from pydub import AudioSegment
    audio = AudioSegment.from_file(input_path)
    diff = target_dBFS - audio.dBFS
    normalized = audio.apply_gain(diff)
    normalized.export(
        output_path, format="wav",
        parameters=['-acodec','pcm_s16le','-ar','16000','-ac','1']
    )

def is_valid_wav(wav_path, min_duration_sec=1.5):
    import wave
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
