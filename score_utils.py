from datetime import datetime, timedelta
from models import ScoreLog
from sqlalchemy import func

def calculate_baseline_and_diff(user_id):
    # 最新スコア
    latest = ScoreLog.query.filter_by(user_id=user_id).order_by(ScoreLog.created_at.desc()).first()
    if not latest:
        return None, None

    # 登録初期5回の平均（ベースライン）
    first_5_scores = ScoreLog.query.filter_by(user_id=user_id).order_by(ScoreLog.created_at).limit(5).all()
    if not first_5_scores or len(first_5_scores) < 3:
        return None, None

    baseline = round(sum(s.score for s in first_5_scores) / len(first_5_scores))
    diff = latest.score - baseline
    return baseline, diff