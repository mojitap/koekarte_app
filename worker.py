import sys
import os
import time
import traceback
from redis import Redis
from rq import Worker, Queue, Connection
from app_instance import app
import tasks  # tasks.py を読み込んでおくことで関数エラーを防止

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

listen_queues = ['default']
redis_url = os.getenv('REDIS_URL') or 'redis://localhost:6379'

while True:
    try:
        redis_conn = Redis.from_url(redis_url)
        with app.app_context():  # FlaskのコンテキストでDB接続等を使用可能にする
            with Connection(redis_conn):
                worker = Worker(map(Queue, listen_queues))
                print("✅ Worker 起動完了。ジョブ待機中...")
                worker.work(logging_level="INFO")
    except Exception as e:
        print("❌ Worker エラー:", e)
        traceback.print_exc()
        print("🔁 5秒後に再起動します...")
        time.sleep(5)
