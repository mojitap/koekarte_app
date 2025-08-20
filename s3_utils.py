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

def s3():
    return _client()

def s3_exists(key: str) -> bool:
    try:
        _client().head_object(Bucket=S3_BUCKET, Key=key)
        return True
    except Exception:
        return False

def s3_object_url(key: str) -> str:
    # バケットが公開設定なら直接アクセス可。非公開なら使えない（参考用）。
    return f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{quote_plus(key)}"

def upload_to_s3(file_path, s3_key, content_type=None, public=True):
    """
    S3 へアップロード。バケットは ACL 無効想定。ACL は一切付けない。
    成功時は、public=True なら公開URL文字列（※非公開だと実アクセス不可）を、
    public=False なら s3_key を返す。失敗時は None。
    """
    try:
        if not content_type:
            content_type = mimetypes.guess_type(s3_key)[0] or 'application/octet-stream'
        extra = {'ContentType': content_type}  # ← ACL を渡さない！
        _client().upload_file(file_path, S3_BUCKET, s3_key, ExtraArgs=extra)
        return s3_object_url(s3_key) if public else s3_key
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
    """バケット非公開の場合の再生用に、署名付きURLを返す"""
    try:
        return _client().generate_presigned_url(
            ClientMethod='get_object',
            Params={'Bucket': S3_BUCKET, 'Key': s3_key},
            ExpiresIn=expires
        )
    except Exception as e:
        print("❌ 署名付きURL生成失敗:", e)
        return None
