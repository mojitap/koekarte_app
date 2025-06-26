# tasks.py
import os
# ① pyAudioAnalysis の vendored redis を避けて、本家 `redis` をインポート
import redis as real_redis
from rq import Queue
from utils.audio_utils import analyze_stress_from_wav as detailed_analyze

# ② 環境変数から Redis URL を取得して本家から接続
redis_url = os.getenv('REDIS_URL')
if redis_url:
    redis_conn = real_redis.from_url(redis_url)
    q = Queue(connection=redis_conn)
else:
    redis_conn = None
    q = None

def enqueue_detailed_analysis(wav_path, user_id):
    if not q:
        print("⚠️ Redis 未設定のため詳細解析ジョブをスキップ")
        return None
    job = q.enqueue(detailed_worker, wav_path, user_id)
    print(f"✅ Enqueued detailed analysis job {job.id}")
    return job.get_id()

def detailed_worker(wav_path, user_id):
    result = detailed_analyze(wav_path)  # ← dictで取得

    from app import db, ScoreLog, User
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone(timedelta(hours=9)))

    log = ScoreLog(
        user_id=user_id,
        timestamp=now,
        score=result["score"],
        is_fallback=result["is_fallback"],
        volume_std=result.get("volume_std"),
        voiced_ratio=result.get("voiced_ratio"),
        zcr=result.get("zcr"),
        pitch_std=result.get("pitch_std"),
        tempo_val=result.get("tempo_val"),
    )
    db.session.add(log)

    # ユーザー情報更新（オプション）
    user = User.query.get(user_id)
    user.last_score = result["score"]
    user.last_recorded = now

    db.session.commit()
    print(f"✅ Detailed analysis complete for user {user_id}, score={result['score']}")
