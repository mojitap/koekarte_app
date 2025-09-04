# --- ① .env は最初に読む ---
from dotenv import load_dotenv
load_dotenv()

import os, time, glob, wave, csv, joblib
import shutil
import numpy as np
import stripe
import python_speech_features
import librosa
import boto3
import ipaddress
import hashlib, secrets, urllib.parse
import redis as real_redis
from utils.subscription_utils import sync_subscription_from_stripe
from pydub import AudioSegment
import imageio_ffmpeg
AudioSegment.converter = imageio_ffmpeg.get_ffmpeg_exe()

from datetime import time as dt_time
from datetime import datetime, date, timedelta, timezone as _tz
UTC = _tz.utc
JST = _tz(timedelta(hours=9))

S3_BUCKET = os.environ.get("S3_BUCKET")
S3_REGION = os.environ.get("S3_REGION", "ap-northeast-1")

import mimetypes
mimetypes.add_type('audio/mpeg', '.mp3')
mimetypes.add_type('audio/mp4',  '.m4a')   # AAC(m4a) はこれ

def s3():
    return boto3.client("s3", region_name=S3_REGION)

def s3_exists(key: str) -> bool:
    try:
        s3().head_object(Bucket=S3_BUCKET, Key=key)
        return True
    except Exception:
        return False

def diary_key_m4a(user_id:int, date_str:str)->str:
    return f"diary/{user_id}/{date_str}.m4a"

def diary_key_mp3(user_id:int, date_str:str)->str:
    return f"diary/{user_id}/{date_str}.mp3"

def _ensure_aware_utc(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

def to_jst(dt):
    dt = _ensure_aware_utc(dt)
    return dt.astimezone(JST) if dt else None

def fmt_jst(dt, fmt='%Y-%m-%d'):
    x = to_jst(dt)
    return x.strftime(fmt) if x else None

from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response, make_response, render_template_string
from flask_cors import CORS
from redis import Redis
from rq import Queue
from app_instance import app, db, login_manager
from tasks import enqueue_detailed_analysis, redis_conn
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mailman import Mail, EmailMessage
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from io import StringIO
from scipy.signal import butter, lfilter
from pyAudioAnalysis import audioBasicIO, MidTermFeatures
from models import User, ScoreLog, ScoreFeedback
from flask_migrate import Migrate
from utils.audio_utils import convert_m4a_to_wav, convert_webm_to_wav, normalize_volume, is_valid_wav, light_analyze
from sqlalchemy.sql import cast, func, text
from sqlalchemy import Date
import json

# ↓↓↓ ③ 定数はインポートしない（必要なら関数だけ）
from s3_utils import upload_to_s3, signed_url
from werkzeug.utils import secure_filename
from utils.log_utils import add_action_log
from rq.job import Job
from routes.iap import iap_bp
from server.mailers import send_contact_via_sendgrid as send_contact, send_password_reset_email
from flask import get_flashed_messages
from urllib.parse import urlparse, urljoin

from flask_babel import Babel, gettext as _
app.config['BABEL_DEFAULT_LOCALE'] = 'ja'
babel = Babel(app)

# ✅ 本番環境かどうか判定（SESSION_COOKIE_SECUREに使用）
IS_PRODUCTION = os.getenv("FLASK_ENV") == "production"

# ───── セッション／クッキー設定 ─────
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['REMEMBER_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_NAME'] = 'session'

if IS_PRODUCTION:
    # 本番環境 (https://koekarte.com) 用
    app.config['SESSION_COOKIE_SECURE']   = True
    app.config['REMEMBER_COOKIE_SECURE']  = True
    app.config['SESSION_COOKIE_DOMAIN'] = '.koekarte.com'
else:
    # ローカル開発環境 (http://localhost:5000 など) 用
    app.config['SESSION_COOKIE_SECURE']   = False
    app.config['REMEMBER_COOKIE_SECURE']  = False
    # Domain を None にすると、アクセスしているホスト名 (localhost) が自動で使われる
    app.config['SESSION_COOKIE_DOMAIN']   = None

# ✅ 設定読み込み
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = os.getenv('SECRET_KEY')
app.logger.debug(f"\ud83d\udd0d SQLALCHEMY_DATABASE_URI = {app.config['SQLALCHEMY_DATABASE_URI']}")

# app.py の設定部に追加
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 1800,  # 30分でコネクションをリサイクル
}

# ✅ DBとアプリを紐付け
migrate = Migrate(app, db)

# 👇この位置に追加
from admin import init_admin
init_admin(app, db)

# ✅ そのほか
app.permanent_session_lifetime = timedelta(days=30)
serializer = URLSafeTimedSerializer(app.secret_key)

app.jinja_env.globals['date'] = date

# メール設定（お名前メール対応版）
app.config['MAIL_SERVER'] = os.getenv("MAIL_SERVER")
app.config['MAIL_PORT'] = int(os.getenv("MAIL_PORT"))
app.config['MAIL_USE_TLS'] = os.getenv("MAIL_USE_TLS") == "True"
app.config['MAIL_USERNAME'] = os.getenv("MAIL_USERNAME")
app.config['MAIL_PASSWORD'] = os.getenv("MAIL_PASSWORD")
app.config['MAIL_DEFAULT_SENDER'] = os.getenv("MAIL_DEFAULT_SENDER")
app.config['CONTACT_RECIPIENT'] = os.getenv("CONTACT_RECIPIENT", app.config['MAIL_DEFAULT_SENDER'])
app.config['MAIL_TIMEOUT'] = int(os.getenv("MAIL_TIMEOUT", "20"))
app.config['MAIL_SUPPRESS_SEND'] = os.getenv("MAIL_SUPPRESS_SEND", "False") == "True"

mail = Mail(app)

# CORS 設定にも開発用オリジンを追加しておくと確実です
CORS(app, origins=[
    "https://koekarte.com",
    "https://koekarte-app.mobile.app",
], supports_credentials=True)

login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def _is_safe_url(target: str) -> bool:
    ref = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, target or ""))
    return test.scheme in ("http", "https") and ref.netloc == test.netloc

@login_manager.unauthorized_handler
def unauthorized():
    # 1) /api/ へのアクセス、または JSON を明示 → JSON 401 を返す
    wants_json = (
        request.path.startswith("/api/") or
        request.is_json or
        request.accept_mimetypes["application/json"] >= request.accept_mimetypes["text/html"]
    )
    if wants_json:
        return jsonify(success=False, error='unauthorized'), 401

    # 2) それ以外（ブラウザ画面）は /login へリダイレクト（戻り先付き）
    next_url = request.url
    if not _is_safe_url(next_url):
        next_url = url_for("dashboard")  # 念のためフォールバック
    return redirect(url_for("login", next=next_url))

app.register_blueprint(iap_bp, url_prefix="/api/iap")

FREE_DAYS = int(os.getenv("FREE_TRIAL_DAYS", "5"))

# パスワードリセット用Blueprint
from server.routes.password import bp as password_bp
app.register_blueprint(password_bp, url_prefix="/api")

# Web側のベースURL（メールに入れるリンク用）
app.config["WEB_BASE_URL"] = os.getenv("WEB_BASE_URL") \
    or os.getenv("DOMAIN_URL") or "https://koekarte.com"

def check_can_use_premium(user):
    now = datetime.now(UTC)

    if getattr(user, "is_paid", False):
        return True, "subscription"   # ← ここが最優先

    if getattr(user, "paid_until", None):
        pu = user.paid_until
        if pu.tzinfo is None:
            pu = pu.replace(tzinfo=UTC)
        if pu >= now:
            return True, "paid"

    if getattr(user, "is_free_extended", False):
        return True, "extended"

    if getattr(user, "created_at", None):
        ca = user.created_at
        if ca.tzinfo is None:
            ca = ca.replace(tzinfo=UTC)
        if ca + timedelta(days=FREE_DAYS) >= now:
            return True, "trial"

    return False, "free"

def can_use_premium(user):
    ok, _ = check_can_use_premium(user)
    return ok

def require_premium(fn):
    @wraps(fn)
    def _wrapped(*args, **kwargs):
        ok, _ = check_can_use_premium(current_user)
        if not ok:
            return jsonify(success=False, error='forbidden'), 403
        return fn(*args, **kwargs)
    return _wrapped

@app.route('/calm')
@login_required
def calm_page():
    # 1) Stripeと突き合わせ
    try:
        sync_subscription_from_stripe(current_user)
    except Exception as e:
        print(f"[PAYWALL SYNC WARN] /calm: {e}")

    # 2) 判定（理由もテンプレへ渡す）
    ok, reason = check_can_use_premium(current_user)
    if not ok:
        flash("⚠️ 無料期間は終了しています。有料登録後にご利用ください。")
        return redirect(url_for('dashboard'), code=303)

    return render_template('calm.html', premium_reason=reason)

@app.route('/music')
@login_required
def music_legacy():
    return redirect(url_for('calm_page'), code=301)

@app.route('/api/music')
@login_required
def api_music():
    return jsonify({"error":"This endpoint was removed."}), 410

# ======== 音声処理 =========
def extract_advanced_features(signal, sr):
    features = {}

    # Pitch（高さ） + 抑揚の変動
    pitches, magnitudes = librosa.piptrack(y=signal, sr=sr)
    pitches_nonzero = pitches[pitches > 0]
    features['pitch_mean'] = np.mean(pitches_nonzero) if pitches_nonzero.size > 0 else 0
    features['pitch_std'] = np.std(pitches_nonzero) if pitches_nonzero.size > 0 else 0

    # MFCC（音色特徴量）
    mfcc = librosa.feature.mfcc(y=signal, sr=sr, n_mfcc=13)
    for i, val in enumerate(np.mean(mfcc, axis=1)):
        features[f'mfcc_{i+1}'] = val

    # 話すスピード（有声音）と無音の割合
    frame_length = 2048
    hop_length = 512
    energy = np.array([
        sum(abs(signal[i:i+frame_length]**2))
        for i in range(0, len(signal), hop_length)
    ])
    threshold = 0.0005
    speech_frames = np.sum(energy > threshold)
    pause_frames = np.sum(energy <= threshold)
    total_frames = speech_frames + pause_frames

    features['speech_rate'] = speech_frames / (len(signal)/sr)
    features['pause_ratio'] = pause_frames / total_frames if total_frames > 0 else 0

    return features

def bandpass_filter(signal, rate, lowcut=300, highcut=3400, order=5):
    nyquist = 0.5 * rate
    low = lowcut / nyquist
    high = highcut / nyquist
    b, a = butter(order, [low, high], btype='band')
    return lfilter(b, a, signal)
    
# ======== ルート定義 =========
@app.route('/send-test-mail')
def send_test_mail():
    email = EmailMessage(
        subject="テスト送信",
        body="MailerSendのSMTP経由で送信されたテストメールです。",
        to=["ta714kadvance@gmail.com"]
    )
    email.send()
    return "メールを送信しました！"

@app.route('/api/contact', methods=['POST'])
def api_contact():
    data = request.get_json(silent=True) or {}
    name    = (data.get('name') or '').strip()
    email   = (data.get('email') or '').strip()
    message = (data.get('message') or '').strip()
    if not (name and email and message):
        return jsonify({'error': 'すべての項目を入力してください'}), 400
    try:
        send_contact(name, email, message)
        return jsonify({'message': '送信成功'}), 201
    except Exception:
        app.logger.exception("contact send failed")
        return jsonify({'error': '送信に失敗しました'}), 502

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        name    = (request.form.get('name') or '').strip()
        email   = (request.form.get('email') or '').strip()
        message = (request.form.get('message') or '').strip()
        if not (name and email and message):
            flash("すべての項目を入力してください。", "error")
            return redirect(url_for('contact'))
        try:
            send_contact(name, email, message)
            flash("お問い合わせを送信しました。ありがとうございます。", "success")
        except Exception:
            app.logger.exception("contact send failed")
            flash("送信に失敗しました。時間を置いてお試しください。", "error")
        return redirect(url_for('contact'))

    # ← GET はフォームを出すだけ。get_flashed_messages() も不要
    return render_template('contact.html')
      
@app.route('/')
def home():
    return render_template('home.html')

@app.route('/confirm/<token>')
def confirm_email(token):
    try:
        email = serializer.loads(token, salt='email-confirm', max_age=3600)
    except:
        return render_template('confirm_failed.html')
    user = User.query.filter_by(email=email).first_or_404()
    if not user.is_verified:
        user.is_verified = True
        db.session.commit()
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = generate_password_hash(request.form['password'])
        gender = request.form.get('gender')
        occupation = request.form.get('occupation')
        prefecture = request.form.get('prefecture')

        # ✅ 生年月日の組み立て
        year = request.form.get('birth_year')
        month = request.form.get('birth_month')
        day = request.form.get('birth_day')
        try:
            birthdate = datetime.strptime(f"{year}-{month}-{day}", "%Y-%m-%d").date()
        except:
            birthdate = None

        # すでに登録済みか確認
        if User.query.filter_by(email=email).first():
            flash('このメールアドレスは既に登録されています。')
            return render_template(
                'register.html',
                username=username,
                email=email,
                gender=gender,
                occupation=occupation,
                prefecture=prefecture,
                birth_year=year,
                birth_month=month,
                birth_day=day
            )

        # 新規ユーザー作成
        user = User(
            username=username, email=email, password=password,
            birthdate=birthdate, gender=gender,
            occupation=occupation, prefecture=prefecture,
            is_verified=True
        )
        db.session.add(user)
        db.session.commit()

        login_user(user)
        session.permanent = True
        # 新規登録直後はまず一回録音してもらう
        return redirect(url_for('record_page'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        identifier = (request.form.get('username') or '').strip()
        password   = request.form.get('password') or ''
        next_url   = request.form.get('next') or ''

        # "None" という文字列で来るケースのガード
        if next_url == 'None':
            next_url = ''

        user = User.query.filter(
            (User.username == identifier) | (User.email == identifier)
        ).first()

        if not user or not check_password_hash(user.password, password):
            # 失敗時はテンプレ再表示（next を保持）
            flash('ログイン失敗', 'error')
            return render_template('login.html', next=next_url), 200

        login_user(user)
        session.permanent = True

        # next がサイト内URLとして安全ならそこへ
        if next_url and _is_safe_url(next_url):
            return redirect(next_url)

        # フォールバック
        return redirect(url_for('dashboard'))

    # GET: ?next= をテンプレに渡す（未指定は空）
    next_url = request.args.get('next') or ''
    return render_template('login.html', next=next_url)
        
@app.route('/export_csv')
@login_required
def export_csv():
    if not can_use_premium(current_user):
        flash("⚠️ 無料期間は終了しました。有料登録後にご利用ください。")
        return redirect(url_for('dashboard'))
    logs = ScoreLog.query.filter_by(user_id=current_user.id).order_by(ScoreLog.timestamp).all()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['日付', 'スコア'])
    for log in logs:
        cw.writerow([log.timestamp.strftime('%Y-%m-%d %H:%M:%S'), log.score])

    output = si.getvalue()
    return Response(output,
                    mimetype="text/csv",
                    headers={"Content-Disposition": "attachment;filename=stress_scores.csv"})

@app.route('/faq')
def faq():
    return render_template('faq.html')

@app.route('/api/score-history')
@login_required
def api_score_history():
    # 必要なら同期（重いなら省略可）
    # try: sync_subscription_from_stripe(current_user)
    # except Exception as e: app.logger.warning(f"[PAYWALL SYNC WARN] {e}")

    ok, _ = check_can_use_premium(current_user)
    if not ok:
        return jsonify(success=False, error='forbidden'), 403

    logs = (ScoreLog.query
            .filter_by(user_id=current_user.id)
            .order_by(ScoreLog.timestamp)
            .all())
    result = [{
        'timestamp': log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
        'score': log.score,
        'is_fallback': log.is_fallback
    } for log in logs]
    return jsonify({"scores": result}), 200

# --- パスワード再設定メール送信（SendGrid版） ---
def send_reset_email(user):
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail

    token = serializer.dumps(user.email, salt='reset-password')
    reset_url = url_for('reset_password', token=token, _external=True, _scheme='https')

    subject = '【コエカルテ】パスワード再設定リンク'
    html = f"""
    <p>{user.username} 様</p>
    <p>以下のリンクよりパスワードの再設定を行ってください（1時間有効）：</p>
    <p><a href="{reset_url}">{reset_url}</a></p>
    <p>※このメールに覚えがない場合は破棄してください。</p>
    """

    msg = Mail(
        from_email=os.getenv("MAIL_DEFAULT_SENDER"),  # 例: noreply@koekarte.com
        to_emails=user.email,
        subject=subject,
        html_content=html,
    )
    try:
        SendGridAPIClient(os.getenv("SENDGRID_API_KEY")).send(msg)
    except Exception:
        app.logger.exception("reset mail send failed")
        # ここで失敗してもユーザー体験的には同じメッセージでOK

# --- パスワードリセット申請ページ ---
# /forgot を GET(フォーム表示) + POST(送信) で受ける
@app.route("/forgot", methods=["GET", "POST"], endpoint="forgot")
def forgot():
    if request.method == "GET":
        return render_template_string("""
<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>パスワードをリセット</title>
  <style>
    :root{ --brand:#007AFF; }
    html,body{ height:100%; margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,"Hiragino Sans","Noto Sans JP",sans-serif; background:#fff; color:#111; }
    .auth-wrap{
      min-height:100svh;
      display:grid; place-items:center;
      padding:24px 16px;
      padding-top:calc(24px + env(safe-area-inset-top));
      padding-bottom:calc(24px + env(safe-area-inset-bottom));
    }
    .auth-card{
      width:100%;
      max-width:560px;
      box-sizing:border-box;   /* ★ padding込みで幅計算（必須） */
      background:#fff; border-radius:12px;
      box-shadow:0 2px 12px rgba(0,0,0,.06);
      padding:20px;
    }
    h2{ font-size:clamp(20px,5vw,24px); margin:0 0 12px; }
    p{  line-height:1.7; color:#444; margin:0 0 16px; }
    .field{ margin:10px 0 0; }
    input[type="email"]{
      width:100%; height:48px; box-sizing:border-box;
      padding:10px 12px; font-size:16px;  /* iOSオートズーム回避 */
      border:1px solid #d0d5dd; border-radius:10px;
    }
    button{
      width:100%; height:48px; margin-top:12px;
      border:0; border-radius:24px; background:var(--brand);
      color:#fff; font-size:17px; font-weight:700; cursor:pointer;
    }
    a.back{ display:inline-block; margin-top:14px; color:var(--brand); text-decoration:underline; }
  </style>
</head>
<body>
  <div class="auth-wrap">
    <div class="auth-card">
      <h2>🔐 パスワードをリセット</h2>
      <p>ご登録のメールアドレスを入力してください。再設定リンクをお送りします。</p>
      <form method="POST">
        <div class="field">
          <input name="email" type="email" placeholder="you@example.com" required />
        </div>
        <button type="submit">送信</button>
      </form>
      <a class="back" href="{{ url_for('login') }}">ログインページへ戻る</a>
    </div>
  </div>
</body>
</html>
        """)

    # --- ここから POST（あなたの既存ロジックを流用） ---
    email = (request.form.get("email") or "").strip().lower()
    if not email:
        flash('メールアドレスを入力してください。', 'error')
        return redirect(url_for('forgot'))

    user = User.query.filter_by(email=email).first()
    # アカウント有無に関わらず同じ応答（存在暴露防止）
    if user:
        token_value = secrets.token_hex(32)
        token_hash  = hashlib.sha256(token_value.encode()).hexdigest()
        expires_at  = datetime.now(UTC) + timedelta(hours=1)

        db.session.execute(text("""
          INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, requested_ip, user_agent)
          VALUES (:uid, :th, :exp, :ip, :ua)
        """), {
          "uid": user.id, "th": token_hash, "exp": expires_at,
          "ip": _client_ip(),
          "ua": request.headers.get("user-agent","")
        })
        db.session.commit()

        base = app.config.get("WEB_BASE_URL", "https://koekarte.com")
        url  = f"{base}/reset-password?token={token_value}&email={urllib.parse.quote(email)}"
        try:
            send_password_reset_email(email, url)
        except Exception as e:
            app.logger.exception("send mail failed: %s", e)

    flash('パスワード再設定用のメールを送信しました（届かない場合は迷惑メールをご確認ください）。', 'info')
    return redirect(url_for('login'))

# --- リセットリンクからの再設定処理 ---
RESET_HTML = """
<!doctype html><meta charset="utf-8" />
<title>パスワード再設定</title>
<style>body{font-family:sans-serif;max-width:520px;margin:40px auto;padding:0 16px}</style>
<h2>パスワード再設定</h2>
<form method="POST">
  <div><input name="new_password" type="password" placeholder="新しいパスワード"
    style="width:100%;padding:10px;margin:8px 0;border:1px solid #ccc;border-radius:4px"></div>
  <button style="padding:10px 16px">更新する</button>
</form>
<p style="color:#06c;">{{ msg or '' }}</p>
"""

@app.route("/reset/<token>", methods=["GET", "POST"])
def reset_legacy(token):
    if request.method == "GET":
        return render_template_string(RESET_HTML, msg="")
    # POST
    new_password = (request.form.get("new_password") or "").strip()
    if not new_password:
        return render_template_string(RESET_HTML, msg="新しいパスワードを入力してください。")
    try:
        # 旧フローで使っていた itsdangerous のトークンを復号
        # （塩を使っていた場合は salt='password-reset' など、必要に応じて合わせてください）
        try:
            email = serializer.loads(token, max_age=3600)  # salt未使用版
        except Exception:
            email = serializer.loads(token, salt='password-reset', max_age=3600)  # 予備
    except (BadSignature, SignatureExpired):
        return render_template_string(RESET_HTML, msg="トークンが無効または期限切れです。")

    user = User.query.filter_by(email=email).first()
    if not user:
        return render_template_string(RESET_HTML, msg="ユーザーが見つかりません。")

    user.password = generate_password_hash(new_password)
    db.session.commit()
    return render_template_string(RESET_HTML, msg="更新しました。ログイン画面に戻ってお試しください。")

@app.get("/reset-password")
def reset_password_page():
    token = request.args.get("token","")
    email = request.args.get("email","")
    html = f"""<!doctype html>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>パスワード再設定</title>
<style>
  :root {{ --gap:12px; }}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
        max-width:640px;margin:24px auto;padding:0 16px;line-height:1.6}}
  h2{{font-size:22px;margin:0 0 var(--gap)}}
  .row{{margin: var(--gap) 0}}
  input[type=password]{{width:100%;padding:14px;font-size:16px;
        border:1px solid #ccc;border-radius:8px}}
  button{{width:100%;padding:14px;font-size:16px;border:0;border-radius:8px;
        background:#0b5ed7;color:#fff}}
  #msg{{margin-top:var(--gap);color:#0b5ed7}}
</style>
<h2>パスワード再設定</h2>
<form id="f">
  <input type="hidden" name="email" value="{email}">
  <input type="hidden" name="token" value="{token}">
  <div class="row"><input name="new_password" type="password" placeholder="新しいパスワード"></div>
  <button>更新する</button>
</form>
<p id="msg"></p>
<script>
  const f = document.getElementById('f');
  f.onsubmit = async (e) => {{
    e.preventDefault();
    const body = Object.fromEntries(new FormData(f).entries());
    const r = await fetch('/api/password/reset', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify(body)
    }});
    const j = await r.json();
    if (j.ok) {{
      // 成功したらログインへ（フラッシュ表示付き）
      location.href = '/login?reset=1';
    }} else {{
      document.getElementById('msg').textContent =
        'トークンが無効か期限切れです。もう一度お試しください。';
    }}
  }};
</script>"""
    return Response(html, mimetype="text/html")

# --- アプリからのパスワード再設定申請用エンドポイント ---
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/api/logout', methods=['POST'])
def api_logout():
    logout_user()
    session.clear()
    resp = jsonify({'message': 'Logged out'})
    resp.set_cookie('session', '', expires=0)  # 明示的にCookie削除
    return resp

@app.route('/api/reset_password', methods=['POST'])
def api_reset_password():
    data = request.get_json(force=True, silent=True) or {}
    token = data.get('token')
    password = data.get('password')
    if not token or not password:
        return jsonify(success=False, message='token と password は必須です'), 400

    try:
        user_id = serializer.loads(token, salt='reset', max_age=3600)  # 例: 1時間有効
    except SignatureExpired:
        return jsonify(success=False, message='リンクの有効期限が切れています'), 400
    except BadSignature:
        return jsonify(success=False, message='不正なリンクです'), 400

    user = User.query.get(user_id)
    if not user:
        return jsonify(success=False, message='ユーザーが見つかりません'), 404

    user.password_hash = generate_password_hash(password)
    db.session.commit()
    return jsonify(success=True)

@app.route('/set-paid/<int:user_id>')
@login_required
def set_paid(user_id):
    if not current_user.is_admin:
        return "アクセス権がありません", 403

    user = User.query.get(user_id)
    if user:
        user.is_paid = True
        db.session.commit()

        # ✅ 操作ログを保存
        from models import ActionLog  # 必要なら上でimport
        log = ActionLog(
            admin_email=current_user.email,
            user_email=user.email,
            action='有料に変更'
        )
        db.session.add(log)
        db.session.commit()

        return redirect(url_for('admin.index'))
    return 'User not found', 404

@app.route('/dashboard')
@login_required
def dashboard():
    # ★ここを追加：画面描画前に一度だけ Stripe と突き合わせ
    try:
        if not current_user.is_paid:
            ok, st = sync_subscription_from_stripe(current_user)
            print(f"[DASH SYNC] ok={ok}, status={st}")
    except Exception as e:
        print(f"[SYNC WARN] {e}")
        
    # ① detailed / fallback で latest を取るコードはそのまま…
    detailed = (
        ScoreLog.query
        .filter_by(user_id=current_user.id, is_fallback=False)
        .order_by(ScoreLog.timestamp.desc())
        .first()
    )
    latest = detailed or (
        ScoreLog.query
        .filter_by(user_id=current_user.id, is_fallback=True)
        .order_by(ScoreLog.timestamp.desc())
        .first()
    )

    # ② ログが一件もないときはダミー値を渡してエラーを防止
    if not latest:
        return render_template(
            "dashboard.html",
            user=current_user,
            message="まだ記録がありません",
            # テンプレートで必ず参照される変数を全部渡す
            first_score=None,
            latest_score=None,
            diff=0,
            first_score_date=None,
            last_date=None,
            baseline=0,
            detailed_ready=False
        )

    # ③ 本来の処理：過去5件でbaselineを計算
    past5 = (
        ScoreLog.query
        .filter_by(user_id=current_user.id)
        .order_by(ScoreLog.timestamp)
        .limit(5)
        .all()
    )
    scores5 = [l.score for l in past5]
    baseline = sum(scores5) // len(scores5) if scores5 else latest.score
    diff = latest.score - baseline

    # JST に変換してからフォーマット
    first_date = fmt_jst(past5[0].timestamp, '%Y-%m-%d') if past5 else fmt_jst(latest.timestamp, '%Y-%m-%d')
    last_date  = fmt_jst(latest.timestamp, '%Y-%m-%d')

    return render_template('dashboard.html',
        user=current_user,
        first_score=past5[0].score if past5 else latest.score,
        latest_score=latest.score,
        diff=diff,
        first_score_date=first_date,
        last_date=last_date,
        baseline=baseline,
        detailed_ready=(detailed is not None)
    )

@app.route('/api/dashboard')
@login_required
def api_dashboard():
    # 1) 詳細済みの最新
    detailed = (
        ScoreLog.query
        .filter_by(user_id=current_user.id, is_fallback=False)
        .order_by(ScoreLog.timestamp.desc())
        .first()
    )
    if detailed:
        latest = detailed
        detailed_ready = True
    else:
        # 2) なければ light（fallback）
        latest = (
            ScoreLog.query
            .filter_by(user_id=current_user.id, is_fallback=True)
            .order_by(ScoreLog.timestamp.desc())
            .first()
        )
        detailed_ready = False

    # ログが一件もない場合にも、必ず同じキーを返す
    if not latest:
        return jsonify({
            'first_score': None,
            'latest_score': None,
            'first_score_date': None,
            'last_date': None,
            'baseline': 0,
            'diff': 0,
            'detailed_ready': False
        }), 200

    # ベースラインは過去5件の平均などで
    past5 = (
        ScoreLog.query
        .filter_by(user_id=current_user.id)
        .order_by(ScoreLog.timestamp)
        .limit(5)
        .all()
    )
    scores5 = [l.score for l in past5]
    baseline = sum(scores5) // len(scores5) if scores5 else latest.score
    diff = latest.score - baseline

    def to_jst(dt):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(JST).strftime('%Y-%m-%d')

    return jsonify({
        'first_score': past5[0].score if past5 else latest.score,
        'latest_score': latest.score,
        'first_score_date': to_jst(past5[0].timestamp) if past5 else to_jst(latest.timestamp),
        'last_date': to_jst(latest.timestamp),
        'baseline': baseline,
        'diff': diff,
        'detailed_ready': detailed_ready
    }), 200

@app.route('/api/forgot-password', methods=['POST'])
def api_forgot_password():
    try:
        data = request.get_json()
        email = data.get('email')

        if not email:
            return jsonify({'error': 'メールアドレスが必要です'}), 400

        user = User.query.filter_by(email=email).first()
        if user:
            send_reset_email(user)  # メール送信時に失敗する可能性がある！

        return jsonify({'message': '再設定メールを送信しました（存在する場合）'}), 200

    except Exception as e:
        print('[forgot-password ERROR]', e)
        return jsonify({'error': '内部エラーが発生しました'}), 500
    
@app.route('/record')
@login_required
def record_page():
    try:
        sync_subscription_from_stripe(current_user)  # ここで最新化
    except Exception as e:
        print(f"[PAYWALL SYNC WARN] {e}")

    if not can_use_premium(current_user):
        flash("⚠️ 無料期間は終了しました。有料登録後にご利用ください。")
        return redirect(url_for('dashboard'))
    return render_template('record.html')

@app.route('/api/record')
@login_required
@require_premium
def record_api():
    return jsonify({"status": "ok"})

@app.route('/api/upload', methods=['POST'])
@login_required
def upload():
    # ---------- 入力チェック ----------
    if 'audio_data' not in request.files:
        return jsonify({'error': '音声データが見つかりません'}), 400

    file = request.files['audio_data']
    if not file.filename:
        return jsonify({'error': 'ファイルが選択されていません'}), 400

    # ---------- 保存先・ファイル名 ----------
    UPLOAD_FOLDER = '/tmp/uploads'
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    now_jst = datetime.now(JST)
    today_jst = now_jst.date()
    now = now_jst

    original_ext = file.filename.rsplit('.', 1)[-1].lower()
    filename = f"user{current_user.id}_{now.strftime('%Y%m%d_%H%M%S')}.{original_ext}"
    save_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(save_path)

    # 元ファイルも S3（任意）
    try:
        # 拡張子→MIME の最低限マップ
        mime_map = {
            'm4a': 'audio/mp4',
            'webm': 'audio/webm',
            'wav': 'audio/wav',
            'mp3': 'audio/mpeg',
        }
        upload_to_s3(save_path, f"raw/{filename}", content_type=mime_map.get(original_ext))
    except Exception:
        app.logger.exception("upload original to s3 failed")

    # ---------- サイズログ ----------
    try:
        size = os.path.getsize(save_path)
        app.logger.info(f"[upload] saved={save_path} size={size}")
        if size < 5000:
            app.logger.warning("[upload] file too small (<5KB) maybe failed recording")
    except Exception:
        app.logger.exception("stat failed")

    # ---------- 変換＆正規化 ----------
    try:
        wav_path = save_path.rsplit('.', 1)[0] + ".wav"
        convert_success = False
        if original_ext == "m4a":
            convert_success = convert_m4a_to_wav(save_path, wav_path)
        elif original_ext == "webm":
            convert_success = convert_webm_to_wav(save_path, wav_path)
        elif original_ext == "wav":
            shutil.copy(save_path, wav_path)
            convert_success = True
        else:
            return jsonify({'error': '対応していないファイル形式です（m4a/webm/wav）'}), 400

        if not convert_success or not is_valid_wav(wav_path):
            return jsonify({'error': '録音が短すぎるか、変換に失敗しました。'}), 400

        # デバッグ保存
        raw_debug_path = os.path.join(os.path.dirname(__file__), 'uploads/raw', os.path.basename(wav_path))
        os.makedirs(os.path.dirname(raw_debug_path), exist_ok=True)
        shutil.copy(wav_path, raw_debug_path)

        # 正規化
        normalized_filename = os.path.basename(wav_path).replace(".wav", "_normalized.wav")
        normalized_path = os.path.join("/tmp", normalized_filename)
        normalize_volume(wav_path, normalized_path)

        # 軽量スコア
        from utils.audio_utils import compute_rms, light_analyze
        raw_rms = compute_rms(wav_path)

        recent = (
            ScoreLog.query
            .filter_by(user_id=current_user.id)
            .filter(ScoreLog.volume_std.isnot(None))
            .order_by(ScoreLog.timestamp.desc())
            .limit(5)
            .all()
        )
        baseline_rms = (sum(x.volume_std for x in recent) / len(recent)) if recent else raw_rms

        quick_score, is_fallback = light_analyze(
            wav_path,
            raw_rms=raw_rms,
            rms_baseline=baseline_rms
        )
    except Exception:
        app.logger.exception("audio pipeline failed")
        return jsonify({'error': '音声処理に失敗しました'}), 500

    # ---------- きょう既存チェック（JST） ----------
    existing = (
        ScoreLog.query
        .filter_by(user_id=current_user.id)
        .filter(cast(func.timezone('Asia/Tokyo', ScoreLog.timestamp), Date) == today_jst)
        .first()
    )

    overwrite = request.args.get('overwrite') == 'true'
    if existing and not overwrite:
        return jsonify({
            'success': False,
            'already': True,
            'message': '本日はすでにスコアを記録済みです。再録音して上書きする場合は OK を押してください。'
        }), 200

    if existing and overwrite:
        db.session.delete(existing)
        db.session.commit()

    # ---------- 永続化（詳細解析用） & RQ ----------
    try:
        persistent_path = os.path.join(os.path.dirname(__file__), 'uploads', os.path.basename(normalized_path))
        os.makedirs(os.path.dirname(persistent_path), exist_ok=True)
        shutil.copy(normalized_path, persistent_path)

        # 正規化WAVも S3（必要なら）
        upload_to_s3(normalized_path, f"normalized/{os.path.basename(normalized_path)}", content_type="audio/wav")

        job_id = enqueue_detailed_analysis(os.path.basename(normalized_path), current_user.id)
        add_action_log(current_user.id, "録音アップロード（light）")
    except Exception:
        app.logger.exception("enqueue failed")
        job_id = None

    # ---------- ★MP3 作成 & S3 ----------
    playback_url = None
    try:
        mp3_path = normalized_path.replace("_normalized.wav", ".mp3")
        # pydub（内部で imageio-ffmpeg が ffmpeg 実行）
        AudioSegment.from_wav(normalized_path).export(mp3_path, format="mp3", bitrate="192k")

        s3_key = f"diary/{current_user.id}/{os.path.basename(mp3_path)}"
        playback_url = upload_to_s3(mp3_path, s3_key, content_type="audio/mpeg")
    except Exception:
        app.logger.exception("make/upload mp3 failed")

    # ---------- DB 保存 ----------
    log = ScoreLog(
        user_id=current_user.id,
        timestamp=now,
        score=quick_score,
        is_fallback=True,
        filename=os.path.basename(normalized_path),
        volume_std=raw_rms,
    )
    db.session.add(log)
    db.session.commit()

    return jsonify({
        'success': True,
        'quick_score': quick_score,
        'job_id': job_id,
        'playback_url': playback_url,
        # 互換キー（古いクライアントが使っている可能性に備える）
        'audio_url': playback_url,
        'url': playback_url
    }), 200

@app.route('/api/upload/result/<job_id>')
@login_required
def upload_result(job_id):
    if not job_id or job_id in ('null', 'undefined'):
        return jsonify(success=False, error='bad_job_id'), 400

    try:
        job = Job.fetch(job_id, connection=redis_conn)
    except Exception:
        # その job_id のジョブが無い
        return jsonify(success=False, error='not_found'), 404

    status = job.get_status()  # 'queued' | 'started' | 'finished' | 'failed' など

    if status == 'finished':
        data = job.result or {}
        # 返却形は tasks 側で決めます。最低限 score を返す想定。
        score = data.get('score') or data.get('final_score')
        return jsonify(status='done', score=score, result=data), 200

    if status in ('failed', 'stopped'):
        return jsonify(status='failed'), 500

    # まだ実行中
    return jsonify(status='pending'), 200
    
# ===== Diary Upload API =====
@app.route('/api/diary/upload', methods=['POST'])
@login_required
@require_premium
def diary_upload():
    try:
        # 1) 日付
        date_str = request.form.get('date') or datetime.now(JST).strftime('%Y-%m-%d')
        try:
            datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            return jsonify({'success': False, 'error': 'bad date'}), 400

        # 2) ファイル
        f = (request.files.get('audio_data')
             or request.files.get('file')
             or request.files.get('audio'))
        if not f:
            return jsonify({'success': False, 'error': 'no file'}), 400

        overwrite = (request.args.get('overwrite') == 'true')
        key_m4a = diary_key_m4a(current_user.id, date_str)
        key_mp3 = diary_key_mp3(current_user.id, date_str)

        # 3) 既存チェック（上書き要求が無ければ already）
        if not overwrite and (s3_exists(key_m4a) or s3_exists(key_mp3)):
            return jsonify({
                'success': False,
                'already': True,
                'message': '本日はすでに日記を保存済みです。上書きする場合は OK を押してください。'
            }), 200

        # 4) 一旦 /tmp に保存
        tmp_dir = '/tmp/diary'
        os.makedirs(tmp_dir, exist_ok=True)
        ext = os.path.splitext(f.filename or '')[1].lower() or '.m4a'
        tmp_in = os.path.join(tmp_dir, f'{current_user.id}-{date_str}{ext}')
        f.save(tmp_in)

        # 5) m4a を S3（ACLなし=public=False）
        ok1 = upload_to_s3(tmp_in, key_m4a, content_type='audio/m4a', public=False)
        if not ok1 and not s3_exists(key_m4a):
            return jsonify({'success': False, 'error': 's3_upload_failed'}), 500

        # 6) mp3 も作成できればアップロード（失敗しても続行）
        try:
            tmp_mp3 = os.path.join(tmp_dir, f'{current_user.id}-{date_str}.mp3')
            AudioSegment.from_file(tmp_in).export(tmp_mp3, format='mp3', bitrate='128k')
            upload_to_s3(tmp_mp3, key_mp3, content_type='audio/mpeg', public=False)
        except Exception as e:
            app.logger.warning(f'[diary_upload] mp3 conversion failed: {e}')

        # 7) 再生URL（どちらか存在する方に対して署名URL）
        key = key_mp3 if s3_exists(key_mp3) else key_m4a
        playback_url = signed_url(key, expires=86400)  # 24h

        return jsonify({'success': True, 'playback_url': playback_url}), 200

    except Exception as e:
        app.logger.exception('diary_upload failed')
        return jsonify({'success': False, 'error': 'server_error', 'detail': str(e)}), 500

# ===== Diary by-date (S3確認) =====
@app.route('/api/diary/by-date')
@login_required
@require_premium
def diary_by_date():
    q = request.args.get('date')
    if not q:
        return jsonify({'error': 'date required'}), 400
    try:
        datetime.strptime(q, "%Y-%m-%d")
    except ValueError:
        return jsonify({'error': 'bad date'}), 400

    key_mp3 = diary_key_mp3(current_user.id, q)
    key_m4a = diary_key_m4a(current_user.id, q)

    key = key_mp3 if s3_exists(key_mp3) else (key_m4a if s3_exists(key_m4a) else None)
    url = signed_url(key, expires=86400) if key else None
    return jsonify({'item': {'date': q, 'playback_url': url}}), 200

@app.route('/api/diary/list')
@login_required
@require_premium
def diary_list():
    limit = max(1, min(int(request.args.get('limit', 30)), 500))
    prefix = f"diary/{current_user.id}/"
    resp = s3().list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)
    contents = resp.get('Contents', [])
    items = []
    for obj in contents:
        key = obj['Key']
        base = os.path.basename(key)
        if not base.endswith('.m4a'):
            continue
        date_str = base.replace('.m4a', '')
        items.append({
            'date': date_str,
            'playback_url': signed_url(key),  # ← 署名URL
            'size': obj.get('Size', 0),
            'last_modified': obj.get('LastModified').isoformat() if obj.get('LastModified') else None,
        })
    items.sort(key=lambda x: x['date'], reverse=True)
    return jsonify({'items': items[:limit]}), 200

@app.route('/api/premium/status')
@login_required
def premium_status():
    try:
        sync_subscription_from_stripe(current_user)  # 任意（重いなら省略）
    except Exception as e:
        app.logger.warning(f"[PAYWALL SYNC WARN] /api/premium/status: {e}")

    ok, reason = check_can_use_premium(current_user)
    return jsonify(ok=ok, reason=reason), 200

@app.route('/result')
@login_required
def result():
    # 1) Stripeと突き合わせ（念のため毎回最新化）
    try:
        sync_subscription_from_stripe(current_user)
    except Exception as e:
        print(f"[PAYWALL SYNC WARN] /result: {e}")

    # 2) 使えるか判定
    if not can_use_premium(current_user):
        flash("⚠️ 無料期間は終了しました。有料登録後にご利用ください。")
        return redirect(url_for('dashboard'), code=303)

    # 3) 以降は既存ロジック
    range_type = request.args.get('range', 'all')
    today = date.today()

    start_date = None
    end_date = None

    if range_type == 'week':
        start_date = today - timedelta(days=7)
    elif range_type == 'this_month':
        start_date = today.replace(day=1)
    elif range_type == 'last_month':
        first_this = today.replace(day=1)
        last_last  = first_this - timedelta(days=1)
        start_date = last_last.replace(day=1)  # 先月1日
        end_date   = first_this                # 今月1日(未満)

    q = ScoreLog.query.filter(ScoreLog.user_id == current_user.id)
    if start_date:
        q = q.filter(ScoreLog.timestamp >= start_date)
    if end_date:
        q = q.filter(ScoreLog.timestamp < end_date)

    logs = q.order_by(ScoreLog.timestamp).all()

    dates  = [log.timestamp.strftime('%m/%d') for log in logs]
    scores = [log.score for log in logs]

    first_five = scores[:5]
    baseline = round(sum(first_five) / len(first_five), 2) if first_five else 0

    return render_template(
        'result.html',
        dates=dates, scores=scores,
        first_score=scores[0] if scores else 0,
        baseline=baseline
    )

@app.route('/admin')
@login_required
def admin():
    if current_user.email != 'ta714kadvance@gmail.com':
        return 'アクセス権がありません', 403

    users = User.query.all()
    for user in users:
        user.score_logs = ScoreLog.query.filter_by(user_id=user.id).order_by(ScoreLog.timestamp).all()
    return render_template('admin.html', users=users)

@app.route('/admin/cleanup')
@login_required
def cleanup_users_without_scores():
    if current_user.email != 'ta714kadvance@gmail.com':
        return 'アクセス権がありません', 403

    users_to_delete = User.query.outerjoin(ScoreLog).filter(ScoreLog.id == None).all()
    deleted_count = 0

    for user in users_to_delete:
        db.session.delete(user)
        deleted_count += 1

    db.session.commit()
    return f"{deleted_count} 件のスコアなしユーザーを削除しました"

@app.route('/admin/set_free_extended/<int:user_id>', methods=['POST'])
@login_required
def set_free_extended(user_id):
    if current_user.email != 'ta714kadvance@gmail.com':
        return "アクセス拒否", 403

    user = User.query.get(user_id)
    user.is_free_extended = not user.is_free_extended
    db.session.commit()
    flash(f"{user.email} の無料延長状態を変更しました。")
    return redirect(url_for('admin'))

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/legal')
def legal():
    return render_template('legal.html')

@app.route('/edit_profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    if request.method == 'POST':
        try:
            data = request.form

            current_user.username = data.get('username', current_user.username)
            
            # 生年月日（文字列 → date型）に安全変換
            birth_str = data.get('birthdate')
            if birth_str:
                try:
                    current_user.birthdate = datetime.strptime(birth_str, "%Y-%m-%d").date()
                except Exception:
                    pass

            current_user.gender = data.get('gender', current_user.gender)
            current_user.occupation = data.get('occupation', current_user.occupation)
            current_user.prefecture = data.get('prefecture', current_user.prefecture)

            db.session.commit()

            flash("プロフィールを更新しました")
            return redirect(url_for('edit_profile'))

        except Exception as e:
            flash("エラーが発生しました: " + str(e))
            return redirect(url_for('edit_profile'))

    return render_template('edit_profile.html', user=current_user)
    
@app.route('/api/register', methods=['POST'])
def api_register():
    try:
        data = request.get_json()
        email = data.get('email')
        username = data.get('username')
        password = data.get('password')
        if not email or not username or not password:
            return jsonify({'error': 'メール・名前・パスワードは必須です'}), 400

        if User.query.filter_by(email=email).first():
            return jsonify({'error': 'このメールアドレスは既に使われています'}), 400

        birthdate = None
        birthdate_str = data.get('birthdate')
        if birthdate_str:
            birthdate = datetime.strptime(birthdate_str, '%Y-%m-%d').date()
        gender     = data.get('gender')
        occupation = data.get('occupation')
        prefecture = data.get('prefecture')

        hashed_pw = generate_password_hash(password)
        user = User(
            email=email,
            username=username,
            password=hashed_pw,
            is_verified=True,
            birthdate=birthdate,
            gender=gender,
            occupation=occupation,
            prefecture=prefecture
        )
        db.session.add(user)
        db.session.commit()

        login_user(user)
        session.permanent = True
        app.logger.debug(f"🔷 login_user() 後の session: {dict(session)}")

        # ✅ 正しいAPIレスポンスを返す
        return jsonify({
            "message": "登録成功",
            "email": user.email,
            "created_at": (user.created_at.replace(tzinfo=UTC) if user.created_at.tzinfo is None else user.created_at).isoformat(),
            "is_paid": bool(user.is_paid),
            "is_free_extended": bool(user.is_free_extended),
        }), 200

    except Exception as e:
        app.logger.error("❌ /api/register 内部エラー:", exc_info=e)
        return jsonify({'error': '登録中にエラーが発生しました'}), 500
    
@app.route('/api/login', methods=['POST'])
def api_login():
    try:
        logout_user()
        
        data = request.get_json()
        identifier = data.get('email')  # フロント側では「email」に入れて送ってる（←identifierと見なす）

        password = data.get('password')

        # メール or ユーザー名で検索
        user = User.query.filter(
            (User.email == identifier) | (User.username == identifier)
        ).first()

        if not user or not check_password_hash(user.password, password):
            return jsonify({'error': 'メールアドレスまたはパスワードが間違っています'}), 401

        login_user(user)
        session.permanent = True

        return jsonify({
            'message': 'ログイン成功',
            'user': {
                'email': user.email,
                'username': user.username,
                'created_at': (_ensure_aware_utc(user.created_at).isoformat() if user.created_at else None),
                'is_paid': user.is_paid,
                'is_free_extended': user.is_free_extended
            }
        })
    except Exception as e:
        print("❌ ログインエラー:", e)
        return jsonify({'error': 'ログイン中にエラーが発生しました'}), 500
        
@app.route('/api/update-profile', methods=['POST'])
def update_profile():
    data = request.get_json()
    is_json = request.is_json

    print("📥 POSTデータ:", data)

    # ✅ 開発者だけはログイン不要で処理許可
    if data and data.get('email') == 'ta714kadvance@gmail.com':
        user = User.query.filter_by(email=data['email']).first()
        if user:
            user.username = data.get('username', user.username)

            birth_str = data.get('birthdate')
            if birth_str:
                try:
                    user.birthdate = datetime.strptime(birth_str, "%Y-%m-%d").date()
                except Exception:
                    pass  # フォーマット不正なら無視

            user.gender = data.get('gender', user.gender)
            user.occupation = data.get('occupation', user.occupation)
            user.prefecture = data.get('prefecture', user.prefecture)

            db.session.commit()
            return jsonify({'message': '✅ 開発者プロフィール更新成功'})

        return jsonify({'error': '開発者アカウントが見つかりません'}), 404

    # ✅ 通常ユーザーはログイン必須
    if not current_user.is_authenticated:
        return jsonify({'error': '未ログインのため更新できません'}), 401

    try:
        # ユーザー情報の更新処理
        current_user.email = data.get('email', current_user.email)
        current_user.username = data.get('username', current_user.username)
        # 生年月日
        birth_str = data.get('birthdate')
        if birth_str:
            try:
                current_user.birthdate = datetime.strptime(birth_str, "%Y-%m-%d").date()
            except Exception:
                pass
        current_user.gender = data.get('gender', current_user.gender)
        current_user.occupation = data.get('occupation', current_user.occupation)
        current_user.prefecture = data.get('prefecture', current_user.prefecture)

        db.session.commit()

        if is_json:
            return jsonify({'message': '✅ 通常プロフィール更新成功'})
        else:
            flash("プロフィールを更新しました")
            return redirect(url_for('profile'))

    except Exception as e:
        if is_json:
            return jsonify({'error': f'プロフィール更新エラー: {str(e)}'}), 400
        else:
            flash("エラーが発生しました")
            return redirect(url_for('profile'))
    
@app.route('/diary')
@login_required
def diary_redirect():
    return render_template('diary.html')

@app.route('/checkout')
@login_required
def checkout():
    return render_template('checkout.html')

# app.py
@app.route('/create-checkout-session', methods=['POST'])
@login_required
def create_checkout_session():
    import os, uuid, stripe
    from datetime import datetime as dt
    from app_instance import db

    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
    price_id = os.getenv("STRIPE_PRICE_ID")
    user = current_user

    # 1) 既存の Stripe Customer をメールで検索 → 無ければ作成 → DBへ保存
    if not getattr(user, 'stripe_customer_id', None):
        cust = None
        try:
            res = stripe.Customer.search(query=f"email:'{user.email}'")
            if res.data:
                cust = res.data[0]
        except Exception:
            res = stripe.Customer.list(email=user.email, limit=1)
            if res.data:
                cust = res.data[0]

        if not cust:
            cust = stripe.Customer.create(
                email=user.email,
                metadata={'user_id': str(user.id)}
            )

        user.stripe_customer_id = cust.id
        db.session.commit()

    # 2) すでに active サブスクがあるならポータルへ
    subs = stripe.Subscription.list(customer=user.stripe_customer_id,
                                    status='active', limit=1)
    if subs.data:
        portal = stripe.billing_portal.Session.create(
            customer=user.stripe_customer_id,
            return_url=url_for('dashboard', _external=True),
        )
        return redirect(portal.url, code=303)

    # 3) Checkout セッション作成
    idem = f"sub_v2_{user.id}_{price_id}"
    session = stripe.checkout.Session.create(
        mode='subscription',
        line_items=[{'price': price_id, 'quantity': 1}],
        customer=user.stripe_customer_id,
        client_reference_id=str(user.id),
        success_url=url_for('checkout_success', _external=True)
                    + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=url_for('dashboard', _external=True),
        idempotency_key=idem,
    )
    return redirect(session.url, code=303)

@app.route('/checkout/success')
@login_required
def checkout_success():
    import stripe, os, datetime as dt
    sid = request.args.get('session_id')
    if not sid:
        return redirect(url_for('dashboard'))

    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
    sess = stripe.checkout.Session.retrieve(sid, expand=['subscription', 'customer'])
    sub  = sess.subscription

    print(f"[SUCCESS] sid={sid} status={getattr(sub,'status',None)} cust={sess.customer} ref={sess.client_reference_id}")

    # 本人確認（client_reference_id 優先。次点で customer 一致）
    if str(current_user.id) != (sess.client_reference_id or str(current_user.id)):
        if getattr(current_user, 'stripe_customer_id', None) != sess.customer:
            print("[SUCCESS] user mismatch -> redirect dashboard")
            return redirect(url_for('dashboard'))

    # DB更新（即 paid 反映）
    current_user.is_paid = bool(sub and sub.status in ('active', 'trialing'))
    current_user.has_ever_paid = True
    current_user.stripe_customer_id = sess.customer or getattr(current_user, 'stripe_customer_id', None)
    current_user.stripe_subscription_id = (sub.id if sub else None)
    current_user.plan_status = (sub.status if sub else None)
    current_user.current_period_end = (
        dt.datetime.fromtimestamp(sub.current_period_end) if getattr(sub, 'current_period_end', None) else None
    )
    db.session.commit()

    return redirect(url_for('dashboard'))

@app.route("/webhook", methods=["POST"])
def stripe_webhook():
    import stripe, os, datetime as dt
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")
    endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET_TEST") if os.getenv("FLASK_ENV") == "development" else os.getenv("STRIPE_WEBHOOK_SECRET")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except Exception:
        return "", 400

    def update_user_by_customer(customer_id, patch: dict):
        user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if not user:
            try:
                c = stripe.Customer.retrieve(customer_id)
                if c and c.email:
                    user = User.query.filter_by(email=c.email).first()
            except Exception:
                pass
        if user:
            for k, v in patch.items():
                setattr(user, k, v)
            db.session.commit()

    t = event['type']
    obj = event['data']['object']

    if t == 'checkout.session.completed':
        sub_id = obj.get('subscription')
        if sub_id:
            sub = stripe.Subscription.retrieve(sub_id)
            update_user_by_customer(
                obj.get('customer'),
                dict(
                    is_paid=sub.status in ('active', 'trialing'),
                    has_ever_paid=True,
                    stripe_subscription_id=sub.id,
                    plan_status=sub.status,
                    current_period_end=dt.datetime.fromtimestamp(sub.current_period_end) if sub.current_period_end else None
                )
            )

    elif t in ('invoice.payment_succeeded', 'customer.subscription.updated'):
        sub = stripe.Subscription.retrieve(obj.get('subscription') or obj.get('id'))
        update_user_by_customer(
            sub.customer,
            dict(
                is_paid=sub.status in ('active', 'trialing'),
                stripe_subscription_id=sub.id,
                plan_status=sub.status,
                current_period_end=dt.datetime.fromtimestamp(sub.current_period_end) if sub.current_period_end else None
            )
        )

    elif t == 'customer.subscription.deleted':
        sub = obj
        update_user_by_customer(
            sub.get('customer'),
            dict(
                is_paid=False,
                plan_status=sub.get('status'),
                stripe_subscription_id=None,
                current_period_end=None
            )
        )

    # 未対応イベント含め最終的に200
    return "", 200

# ✅ 無制限メールアドレスリスト（漏洩リスクに備えて限定的に）
ALLOWED_FREE_EMAILS = ['ta714kadvance@gmail.com']

@app.route('/api/profile')
def api_profile():
    """
    - baseline: ユーザー登録から5日間の中で『最初の最大5回』の平均
      * 5日間に2〜4回しか記録がなくても、その回数で平均
      * 5日間に記録が1件も無い場合は、全期間の『最初の最大5回』でフォールバック
    - last_score: 今日(JST)の最新スコア。無ければ直近スコア
    - score_deviation: last_score - baseline
    """
    if not current_user.is_authenticated:
        return jsonify({
            'error': '未ログイン状態です',
            'email': None,
            'is_paid': False,
            'is_free_extended': False,
            'created_at': None
        }), 401

    can_use, reason = check_can_use_premium(current_user)

    # --- スコア抽出（列名ゆらぎ対応） ---
    def pick_score(row):
        if not row:
            return None
        for key in ('score', 'total_score', 'stress_score'):
            if hasattr(row, key):
                v = getattr(row, key)
                if v is not None:
                    try:
                        return float(v)
                    except Exception:
                        return None
        return None

    uid = current_user.id

    # --- 今日(JST)の範囲をUTCへ ---
    today_jst = datetime.now(JST).date()
    start_jst = datetime.combine(today_jst, dt_time(0, 0, 0), tzinfo=JST)
    end_jst   = start_jst + timedelta(days=1)
    start_utc = start_jst.astimezone(UTC)
    end_utc   = end_jst.astimezone(UTC)

    # 今日(JST)の最新スコア
    today_logs = (
        ScoreLog.query
        .filter_by(user_id=uid)
        .filter(ScoreLog.timestamp >= start_utc, ScoreLog.timestamp < end_utc)
        .order_by(ScoreLog.timestamp.desc())
        .all()
    )
    today_score_value = next((pick_score(x) for x in today_logs if pick_score(x) is not None), None)

    # 直近の1件（表示用）
    last_log = (
        ScoreLog.query
        .filter_by(user_id=uid)
        .order_by(ScoreLog.timestamp.desc())
        .first()
    )
    last_recorded = fmt_jst(last_log.timestamp, '%Y-%m-%d %H:%M:%S') if last_log else None
    last_score_val = pick_score(last_log)

    # --- baseline: 登録から5日間の「最初の最大5回」 ---
    ca_utc = _ensure_aware_utc(current_user.created_at) if current_user.created_at else None
    if ca_utc:
        trial_end_utc = ca_utc + timedelta(days=5)
        first_within_trial = (
            ScoreLog.query
            .filter_by(user_id=uid)
            .filter(ScoreLog.timestamp >= ca_utc, ScoreLog.timestamp < trial_end_utc)
            .order_by(ScoreLog.timestamp.asc())
            .limit(5)
            .all()
        )
    else:
        first_within_trial = []

    baseline_candidates = first_within_trial or (
        ScoreLog.query
        .filter_by(user_id=uid)
        .order_by(ScoreLog.timestamp.asc())
        .limit(5)
        .all()
    )

    base_vals = [v for v in (pick_score(x) for x in baseline_candidates) if v is not None]
    baseline = round(sum(base_vals)/len(base_vals), 1) if base_vals else None

    # 偏差 = （今日 or 直近） - baseline
    base_for_dev = today_score_value if today_score_value is not None else last_score_val
    score_dev = (round(base_for_dev - baseline, 1)
                 if (base_for_dev is not None and baseline is not None)
                 else None)

    return jsonify({
        'email': current_user.email,
        'username': current_user.username,
        'birthdate': current_user.birthdate.isoformat() if current_user.birthdate else None,
        'gender': current_user.gender,
        'occupation': current_user.occupation,
        'prefecture': current_user.prefecture,
        'created_at': _ensure_aware_utc(current_user.created_at).isoformat() if current_user.created_at else None,
        'last_score': base_for_dev,
        'last_recorded': last_recorded,
        'baseline': baseline,
        'score_deviation': score_dev,
        'is_paid': current_user.is_paid,
        'is_free_extended': current_user.is_free_extended,
        'paid_until': _ensure_aware_utc(current_user.paid_until).isoformat() if current_user.paid_until else None,
        'paid_platform': current_user.paid_platform,
        'can_use_premium': can_use,
        'premium_reason': reason,
        'next_renewal_at': _ensure_aware_utc(current_user.paid_until).isoformat() if current_user.paid_until else None,
    })
    
try:
    with app.app_context():
        time.sleep(3)  # ← ⭐️ここで3秒だけ待つ
        db.create_all()
except Exception as e:
    print("❌ データベース接続に失敗しました:", e)

@app.route('/api/scores')
@login_required
def api_scores():
    rng = (request.args.get('range') or 'all').lower()
    # JST で日付境界を切る
    today_jst = datetime.now(JST).date()
    start = None
    if rng in ('last_7d', 'last7', 'week', '7d', '直近1週間'):
        start = today_jst - timedelta(days=7)
    elif rng in ('this_month', '今月'):
        start = today_jst.replace(day=1)
    elif rng in ('last_month', '先月'):
        first_this = today_jst.replace(day=1)
        start = (first_this - timedelta(days=1)).replace(day=1)
        end   = first_this  # 先月末まで
    # クエリ組み立て
    q = ScoreLog.query.filter(ScoreLog.user_id == current_user.id)
    if start:
        # JSTで比較（DBはUTC想定）
        q = q.filter(cast(func.timezone('Asia/Tokyo', ScoreLog.timestamp), Date) >= start)
    if rng in ('last_month', '先月'):
        q = q.filter(cast(func.timezone('Asia/Tokyo', ScoreLog.timestamp), Date) < end)
    logs = q.order_by(ScoreLog.timestamp).all()

    # 表示用配列
    scores = [{
        'date': fmt_jst(log.timestamp, '%Y-%m-%d'),
        'score': log.score,
        'is_fallback': log.is_fallback
    } for log in logs]

    # ★ グローバル（全期間）の“最初の5件”平均：常に同じ
    first5_all = (
        ScoreLog.query
        .filter(ScoreLog.user_id == current_user.id)
        .order_by(ScoreLog.timestamp.asc())
        .limit(5).all()
    )
    global_baseline = round(sum(x.score for x in first5_all)/len(first5_all), 1) if first5_all else 0

    # ★ 表示期間内の最新と、その差（常に global_baseline と比較）
    latest_score = scores[-1]['score'] if scores else 0
    diff_global  = round(latest_score - global_baseline, 1)

    return jsonify({
        'range': rng,
        'scores': scores,
        'global_baseline': global_baseline,
        'latest': latest_score,
        'diff_against_global': diff_global
    }), 200

@app.route('/create-admin')
def create_admin():
    from werkzeug.security import generate_password_hash
    from models import db, User

    # すでに存在していたらスキップ
    existing = User.query.filter_by(email='ta714kadvance@gmail.com').first()
    if existing:
        return 'すでに作成済みです'

    user = User(
        email='ta714kadvance@gmail.com',
        username='管理者',
        password=generate_password_hash('taka0714'),
        is_verified=True
    )
    db.session.add(user)
    db.session.commit()
    return '管理者ユーザーを作成しました'

@app.route('/api/feedback', methods=['POST'])
@login_required
def api_feedback():
    data = request.get_json() or {}
    internal = data.get('internal')
    user_score = data.get('user')
    if internal is None or user_score is None:
        return jsonify({'error': 'invalid payload'}), 400

    try:
        fb = ScoreFeedback(
            user_id=current_user.id,
            internal=internal,
            user_score=user_score
        )
        db.session.add(fb)
        db.session.commit()
        return jsonify({'message': 'OK'}), 200
    except Exception as e:
        print("❌ feedback error:", e)
        return jsonify({'error': 'server error'}), 500

@app.route('/admin/upgrade-db')
def upgrade_db():
    from flask_migrate import upgrade
    try:
        upgrade()
        return "✅ DB upgrade executed successfully", 200
    except Exception as e:
        return f"❌ Error: {e}", 500

@app.route('/enqueue')
def enqueue_test():
    from tasks import enqueue_detailed_analysis
    test_path = "/tmp/uploads/test.wav"
    user_id = 1  # 実際に存在するID
    job_id = enqueue_detailed_analysis(test_path, user_id)
    return f"ジョブを送信しました: {job_id}"

# ✅ ローカルでも本番でも動くDB初期化
try:
    with app.app_context():
        db.create_all()
except Exception as e:
    print("❌ DB作成エラー:", e)

@app.route('/api/job_status/<job_id>')
@login_required
def job_status(job_id):
    job = Job.fetch(job_id, connection=redis_conn)
    if job.is_finished:
        scorelog = (
            ScoreLog.query
            .filter_by(
                user_id=current_user.id,
                filename=job.args[0],      # enqueue時の第一引数が filename
                is_fallback=False
            )
            .first()
        )
        return jsonify({'status': 'finished', 'score': scorelog.score}), 200
    if job.is_failed:
        return jsonify({'status': 'failed'}), 200
    return jsonify({'status': 'running'}), 200

@app.get("/api/ping")
def ping():
    return {"ok": True}

@app.errorhandler(404)
def handle_404(e):
    if request.path.startswith('/api/'):
        return jsonify(success=False, error='not_found'), 404
    return render_template('error.html', code=404, message='ページが見つかりません'), 404

@app.errorhandler(500)
def handle_500(e):
    app.logger.exception("500 error")
    if request.path.startswith('/api/'):
        return jsonify(success=False, error='server_error'), 500
    return render_template('error.html', code=500, message='サーバーエラーが発生しました'), 500

def _client_ip():
    # X-Forwarded-For は "real, proxy1, proxy2" という形になることが多い
    xf = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    cand = xf or (request.remote_addr or "")
    try:
        return str(ipaddress.ip_address(cand))  # ここで単一IPに正規化（無効なら except）
    except Exception:
        return None  # INET列に入らない値は NULL にする

# ✅ ローカル起動用（Renderでは無視される）
if __name__ == '__main__':
    app.run(debug=True)
