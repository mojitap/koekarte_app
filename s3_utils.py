# s3_utils.py
import boto3
import os
from botocore.exceptions import NoCredentialsError

AWS_ACCESS_KEY = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
S3_BUCKET = 'koekarte-up'
S3_REGION = 'ap-northeast-1'  # ← 東京リージョンに修正

def download_from_s3(s3_key, local_path):
    try:
        s3 = boto3.client('s3',
                          aws_access_key_id=AWS_ACCESS_KEY,
                          aws_secret_access_key=AWS_SECRET_KEY,
                          region_name=S3_REGION)
        s3.download_file(S3_BUCKET, s3_key, local_path)
        print("✅ S3ダウンロード成功:", s3_key)
        return True
    except Exception as e:
        print("❌ S3ダウンロード失敗:", e)
        return False
