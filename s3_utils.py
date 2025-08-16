# s3_utils.py
import os
import boto3
from botocore.exceptions import NoCredentialsError
from urllib.parse import quote_plus

AWS_ACCESS_KEY = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
S3_BUCKET = os.getenv('S3_BUCKET', 'koekarte-up')
S3_REGION = os.getenv('S3_REGION', 'ap-northeast-1')  # 例: ap-northeast-1

def _client():
    return boto3.client(
        's3',
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
        region_name=S3_REGION
    )

def _public_url(key: str) -> str:
    # Virtual-hosted–style URL
    # https://<bucket>.s3.<region>.amazonaws.com/<key>
    return f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{quote_plus(key)}"

def upload_to_s3(file_path: str, s3_key: str, content_type: str | None = None, public: bool = True):
    """
    file_path のファイルを S3 にアップロード。
    成功時はパブリックURL（public=Trueの場合）または None を返す。
    """
    try:
        s3 = _client()
        extra = {}
        if content_type:
            extra["ContentType"] = content_type
        if public:
            extra["ACL"] = "public-read"

        if extra:
            s3.upload_file(file_path, S3_BUCKET, s3_key, ExtraArgs=extra)
        else:
            s3.upload_file(file_path, S3_BUCKET, s3_key)

        print("✅ S3アップロード成功:", s3_key)
        return _public_url(s3_key) if public else None
    except NoCredentialsError:
        print("❌ AWS認証情報が見つかりません")
        return None
    except Exception as e:
        print("❌ S3アップロード失敗:", e)
        return None

def download_from_s3(s3_key: str, local_path: str):
    try:
        s3 = _client()
        s3.download_file(S3_BUCKET, s3_key, local_path)
        print("✅ S3ダウンロード成功:", s3_key)
        return True
    except Exception as e:
        print("❌ S3ダウンロード失敗:", e)
        return False

# （オプション）バケットを非公開のまま再生したい場合は、署名付きURLを発行して返す関数
def signed_url(s3_key: str, expires: int = 3600) -> str | None:
    try:
        s3 = _client()
        return s3.generate_presigned_url(
            ClientMethod='get_object',
            Params={'Bucket': S3_BUCKET, 'Key': s3_key},
            ExpiresIn=expires
        )
    except Exception as e:
        print("❌ 署名付きURL生成失敗:", e)
        return None
