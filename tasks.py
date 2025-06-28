import os
import redis as real_redis
from rq import Queue
from utils.audio_utils import analyze_stress_from_wav as detailed_analyze
from app_instance import app, db  # ✅ ← これが正解！
from models import ScoreLog, User, ActionLog
from datetime import datetime, timedelta, timezone

# Redis接続
redis_url = os.getenv('REDIS_URL')
if redis_url:
    redis_conn = real_redis.from_url(redis_url)
    q = Queue('default', connection=redis_conn)
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
    print(f"🚀 detailed_worker START: user_id={user_id}, path={wav_path}")

    result = detailed_analyze(wav_path)
    print(f"🎯 analyze result = {result}")

    with app.app_context():
        print("📝 DB書き込み処理に入ります")

        jst = timezone(timedelta(hours=9))
        now = datetime.now(jst)

        window_start = now - timedelta(minutes=5)
        window_end = now + timedelta(minutes=1)

        log = ScoreLog.query.filter(
            ScoreLog.user_id == user_id,
            ScoreLog.timestamp >= window_start,
            ScoreLog.timestamp <= window_end
        ).order_by(ScoreLog.timestamp.desc()).first()

        if not log:
            print(f"❌ ScoreLog が見つかりません: user_id={user_id}, 時刻範囲: {window_start}〜{window_end}")
            return

        # スコア情報更新
        log.score = result["score"]
        log.is_fallback = bool(result.get("is_fallback", True))
        log.volume_std = result.get("volume_std")
        log.voiced_ratio = result.get("voiced_ratio")
        log.zcr = result.get("zcr")
        log.pitch_std = result.get("pitch_std")
        log.tempo_val = result.get("tempo_val")

        user = User.query.get(user_id)
        user.last_score = result["score"]
        user.last_recorded = now

        log_action = ActionLog(
            admin_email=None,
            user_email=user.email,
            action=f"詳細スコア解析を実行（score={result['score']}）",
            timestamp=now
        )
        db.session.add(log_action)
        db.session.commit()

        print(f"✅ 詳細解析＆上書き完了 for user {user_id}, score={result['score']}")
