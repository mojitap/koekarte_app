# tasks.py
from redis import Redis
from rq import Queue
from utils.audio_utils import detailed_analyze  # ①〜⑤全部版

redis_conn = Redis()        # Redisの接続設定に合わせて
q = Queue(connection=redis_conn)

def enqueue_detailed_analysis(wav_path, user_id):
    job = q.enqueue(detailed_worker, wav_path, user_id)
    return job.get_id()

def detailed_worker(wav_path, user_id):
    score, _ = detailed_analyze(wav_path)
    # DBに書き戻すロジック
    from app import db, ScoreLog, User
    now = datetime.now(timezone(timedelta(hours=9)))
    log = ScoreLog(user_id=user_id, timestamp=now, score=score, is_fallback=False)
    db.session.add(log)
    user = User.query.get(user_id)
    user.last_score = score
    user.last_recorded = now
    db.session.commit()