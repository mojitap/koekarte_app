# worker.py（ルートディレクトリに新規作成）
from redis import Redis
from rq import Worker, Queue, Connection
from app_instance import app
import tasks  # ← これで tasks.py の関数を使える

redis_url = os.getenv('REDIS_URL') or 'redis://localhost:6379'
redis_conn = Redis.from_url(redis_url)

with app.app_context():  # Flaskアプリのコンテキストで実行
    with Connection(redis_conn):
        worker = Worker(['default'])
        worker.work()