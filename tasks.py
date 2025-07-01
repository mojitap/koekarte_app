import os
import redis as real_redis
from rq import Queue
from app_instance import app, db
from datetime import datetime, timedelta, timezone
from s3_utils import download_from_s3
from utils.log_utils import add_action_log

# Redis 接続
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
    from models import ScoreLog, User, ActionLog
    # 遅延インポートしてメモリを節約
    from utils.audio_utils import light_analyze as detailed_analyze

    print(f"🚀 detailed_worker START: user_id={user_id}, s3_key={s3_key}")
    local_path = f"/tmp/{os.path.basename(s3_key)}"

    # S3 からダウンロード
    if not download_from_s3(s3_key, local_path):
        print(f"❌ S3からのダウンロード失敗: {s3_key}")
        return

    # 音声解析
    try:
        score, is_fallback = detailed_analyze(local_path)
    except Exception as e:
        print(f"❌ analyze error: {e}")
        return
    print(f"🎯 analyze result = score={score}, is_fallback={is_fallback}")

    if is_fallback:
        print("⚠️ fallbackスコアのため、score_logは上書きしません")
        return

    # DB 更新
    with app.app_context():
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(minutes=5)
        window_end   = now + timedelta(minutes=1)

        log = ScoreLog.query.filter(
            ScoreLog.user_id == user_id,
            ScoreLog.timestamp.between(window_start, window_end)
        ).order_by(ScoreLog.timestamp.desc()).first()

        user = User.query.get(user_id)

        if not log:
            # 見つからないならログだけ残して終了
            action = f"詳細スコア解析試行（ScoreLog見つからず、score={score}）"
            add_action_log(user_id, action)
            return

        # スコア更新
        log.score = score
        log.is_fallback = False
        if user:
            user.last_score = score
            user.last_recorded = now

        add_action_log(user_id, f"詳細スコア解析完了（score={score}）")
        db.session.commit()
        print(f"✅ 詳細解析＆上書き完了 for user {user_id}, score={score}")
