import os
import redis as real_redis
from rq import Queue
from utils.audio_utils import analyze_stress_from_wav as detailed_analyze
from app_instance import app, db  # ✅ ← これが正解！
from models import ScoreLog, User, ActionLog
from datetime import datetime, timedelta, timezone
from s3_utils import download_from_s3
from utils.log_utils import add_action_log

# Redis接続
redis_url = os.getenv('REDIS_URL')
if redis_url:
    redis_conn = real_redis.from_url(redis_url)
    q = Queue('default', connection=redis_conn)
else:
    redis_conn = None
    q = None

def enqueue_detailed_analysis(s3_filename, user_id):
    if not q:
        print("⚠️ Redis 未設定のため詳細解析ジョブをスキップ")
        return None
    print(f"📤 Redis にジョブ登録中: user_id={user_id}, filename={s3_filename}")
    job = q.enqueue(detailed_worker, s3_filename, user_id)
    print(f"✅ Redis 登録完了: job.id={job.id}")
    return job.get_id()

def detailed_worker(s3_key, user_id):
    print(f"🚀 detailed_worker START: user_id={user_id}, s3_key={s3_key}")

    local_path = f"/tmp/{os.path.basename(s3_key)}"

    # ✅ S3からダウンロード
    if not download_from_s3(s3_key, local_path):
        print(f"❌ S3からのダウンロード失敗: {s3_key}")
        return

    if not os.path.exists(local_path):
        print(f"❌ ファイルが存在しません: {local_path}")
        return

    result = detailed_analyze(local_path)
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

        user = User.query.get(user_id)  # ここで先に取得しておく

        if not log:
            print(f"❌ ScoreLog が見つかりません: user_id={user_id}, 時刻範囲: {window_start}〜{window_end}")
            if user:
                log_action = ActionLog(
                    admin_email=None,
                    user_email=user.email,
                    action=f"詳細スコア解析を試行（ScoreLog見つからず、score={result['score']}）",
                    timestamp=now
                )
                db.session.add(log_action)
                db.session.commit()
            return

        # スコア情報更新
        log.score = result["score"]
        log.is_fallback = bool(result.get("is_fallback", True))
        log.volume_std = result.get("volume_std")
        log.voiced_ratio = result.get("voiced_ratio")
        log.zcr = result.get("zcr")
        log.pitch_std = result.get("pitch_std")
        log.tempo_val = result.get("tempo_val")

        if user:
            user.last_score = result["score"]
            user.last_recorded = now

        log_action = ActionLog(
            admin_email=None,
            user_email=user.email if user else None,
            action=f"詳細スコア解析を実行（score={result['score']}）",
            timestamp=now
        )
        db.session.add(log_action)
        db.session.commit()

        print(f"✅ 詳細解析＆上書き完了 for user {user_id}, score={result['score']}")
