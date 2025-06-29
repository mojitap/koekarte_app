# s3_utils.py
import boto3
import os
from botocore.exceptions import NoCredentialsError

AWS_ACCESS_KEY = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
S3_BUCKET = 'koekarte-up'
S3_REGION = 'ap-northeast-1'  # ← 東京リージョンに修正

def upload_to_s3(file_path, s3_key):
    try:
        s3 = boto3.client('s3',
                          aws_access_key_id=AWS_ACCESS_KEY,
                          aws_secret_access_key=AWS_SECRET_KEY,
                          region_name=S3_REGION)

        s3.upload_file(file_path, S3_BUCKET, s3_key)
        print("✅ S3アップロード成功:", s3_key)
        return True
    except NoCredentialsError:
        print("❌ AWS認証情報が見つかりません")
        return False