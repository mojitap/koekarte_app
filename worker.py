import sys
import os
import time
import traceback
from redis import Redis
from rq import Worker, Queue, Connection
from app_instance import app
import tasks  # tasks.py を読み込んでおくことで関数エラーを防止
import boto3

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

def download_from_s3(s3_key, local_path):
    s3 = boto3.client('s3',
                      aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                      aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
                      region_name='us-east-1')

    s3.download_file('koekarte-uploads', s3_key, local_path)
    print("✅ S3からファイルを取得:", local_path)
