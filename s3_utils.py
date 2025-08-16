# s3_utils.py
import os
import mimetypes
import boto3
from urllib.parse import quote_plus
from botocore.exceptions import NoCredentialsError, ClientError

AWS_ACCESS_KEY = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
S3_BUCKET     = os.getenv('S3_BUCKET', 'koekarte-up')
S3_REGION     = os.getenv('AWS_REGION', 'ap-northeast-1')  # 東京

def _client():
    return boto3.client(
        's3',
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
        region_name=S3_REGION
    )

def _public_url(key: str) -> str:
    # Virtual-hosted–style
    return f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{quote_plus(key)}"

def upload_to_s3(file_path, s3_key, content_type=None, public=True):
    """成功時は “公開URL or 署名付きURL”、失敗時は None を返す"""
    try:
        s3 = _client()

        if not content_type:
            content_type = mimetypes.guess_type(s3_key)[0] or 'application/octet-stream'

        extra = {'ContentType': content_type}
        if public:
            extra['ACL'] = 'public-read'

        s3.upload_file(file_path, S3_BUCKET, s3_key, ExtraArgs=extra)

        # public=True の場合は公開URLを返す
        return _public_url(s3_key) if public else s3_key
    except (NoCredentialsError, ClientError) as e:
        print("❌ S3アップロード失敗:", repr(e))
        return None

def download_from_s3(s3_key: str, local_path: str):
    try:
        _client().download_file(S3_BUCKET, s3_key, local_path)
        print("✅ S3ダウンロード成功:", s3_key)
        return True
    except Exception as e:
        print("❌ S3ダウンロード失敗:", e)
        return False

def signed_url(s3_key: str, expires: int = 3600):
    """バケット非公開で再生したい場合に署名付きURLを返す"""
    try:
        return _client().generate_presigned_url(
            ClientMethod='get_object',
            Params={'Bucket': S3_BUCKET, 'Key': s3_key},
            ExpiresIn=expires
        )
    except Exception as e:
        print("❌ 署名付きURL生成失敗:", e)
        return None
