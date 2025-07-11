import os, time, glob, wave, csv, joblib
import shutil
import numpy as np
import stripe
import python_speech_features
import librosa
import redis as real_redis
from datetime import datetime, date, timedelta, timezone as _tz
UTC = _tz.utc
JST = _tz(timedelta(hours=9))
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response, make_response
from flask_cors import CORS
from redis import Redis
from rq import Queue
from app_instance import app, db, login_manager
from tasks import enqueue_detailed_analysis, redis_conn
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mailman import Mail, EmailMessage
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from dotenv import load_dotenv
from io import StringIO
from scipy.signal import butter, lfilter
from pydub import AudioSegment
from pyAudioAnalysis import audioBasicIO, MidTermFeatures
from models import User, ScoreLog, ScoreFeedback
from flask_migrate import Migrate
from utils.audio_utils import convert_m4a_to_wav, convert_webm_to_wav, normalize_volume, is_valid_wav, light_analyze
from utils.auth_utils import check_can_use_premium
from sqlalchemy.sql import cast, func, text
from sqlalchemy import Date
import json
from s3_utils import upload_to_s3
from werkzeug.utils import secure_filename
from utils.log_utils import add_action_log
from rq.job import Job

# .env 読み込み（FLASK_ENV の取得より先）
load_dotenv()

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

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        message = request.form['message']

        email_msg = EmailMessage(
            subject="【koekarte】お問い合わせ",
            body=f"""【お問い合わせ】
名前: {name}
メール: {email}

内容:
{message}
""",
            to=['koekarte.info@gmail.com'],
            from_email='noreply@koekarte.com'  # 明示的に指定
        )
        email_msg.send()
        flash("お問い合わせを送信しました。ありがとうございます。")
        return redirect(url_for('contact'))

    return render_template('contact.html')

@app.route('/api/contact', methods=['POST'])
def api_contact():
    data = request.get_json()
    print("📩 API受信データ:", data)

    name = data.get('name')
    email = data.get('email')
    message = data.get('message')

    if not all([name, email, message]):
        print("⚠️ 不完全なデータ:", data)
        return jsonify({'error': 'すべての項目を入力してください'}), 400

    try:
        email_msg = EmailMessage(
            subject="【koekarte】お問い合わせ",
            body=f"""【お問い合わせ】
名前: {name}
メール: {email}

内容:
{message}
""",
            to=["koekarte.info@gmail.com"],
            from_email=app.config['MAIL_DEFAULT_SENDER']
        )
        print("📤 メール送信準備完了:", email_msg)
        email_msg.send()
        print("✅ メール送信成功")
        return jsonify({'message': '送信成功'})
    except Exception as e:
        print("❌ メール送信失敗:", e)
        return jsonify({'error': '送信に失敗しました'}), 500
      
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

from datetime import datetime

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
        print("📥 request.form:", request.form)

        identifier = request.form.get('username')
        password = request.form.get('password')

        print(f"入力値: identifier={identifier}, password={password}")

        user = User.query.filter((User.username == identifier) | (User.email == identifier)).first()

        if not user:
            print("❌ 該当ユーザーなし")
            return 'ログイン失敗'
        if not check_password_hash(user.password, password):
            print("❌ パスワード不一致")
            return 'ログイン失敗'

        login_user(user)
        session.permanent = True
        print("✅ ログイン成功:", current_user.is_authenticated)

        return redirect(url_for('dashboard'))

    return render_template('login.html')
        
@app.route('/export_csv')
@login_required
def export_csv():
    if not check_can_use_premium(current_user):
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
    print("🪪 current_user.id =", current_user.id)
    logs = ScoreLog.query.filter_by(user_id=current_user.id).order_by(ScoreLog.timestamp).all()

    print(f"📊 ログ件数 = {len(logs)}")
    for log in logs:
        print(f"📝 {log.timestamp}: {log.score}")

    result = [
        {
            'timestamp': log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'score': log.score, 
            'is_fallback': log.is_fallback  # ← 追加
        }
        for log in logs
    ]
    return jsonify({ "scores": result }), 200

# --- パスワード再設定メール送信 ---
def send_reset_email(user):
    token = serializer.dumps(user.email, salt='reset-password')
    reset_url = url_for('reset_password', token=token, _external=True, _scheme='https')

    subject = '【コエカルテ】パスワード再設定リンク'
    message = f"""{user.username} 様

以下のリンクよりパスワードの再設定を行ってください：
{reset_url}

このリンクは1時間で無効になります。

※このメールに覚えがない場合は無視してください。
"""

    msg = EmailMessage(subject, message, to=[user.email])
    msg.send()

# --- パスワードリセット申請ページ ---
@app.route('/forgot', methods=['GET', 'POST'])
def forgot():
    if request.method == 'POST':
        email = request.form['email']
        user = User.query.filter_by(email=email).first()
        if user:
            send_reset_email(user)
        flash("パスワード再設定用のリンクを送信しました")
        return redirect(url_for('login'))
    return render_template('forgot.html')

# --- リセットリンクからの再設定処理 ---
@app.route('/reset/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        email = serializer.loads(token, salt='reset-password', max_age=3600)
    except (SignatureExpired, BadSignature):
        return 'リンクが無効または期限切れです'

    user = User.query.filter_by(email=email).first_or_404()

    if request.method == 'POST':
        new_password = request.form['password']
        user.password = generate_password_hash(new_password)
        db.session.commit()
        flash("パスワードを更新しました")
        return render_template('reset_done.html')  # ✅ パスワード更新後に表示！

    return render_template('reset.html')  # ✅ 最初は入力フォーム！

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
    def to_jst(dt):
        # DB には UTC で保存されている想定。naive（tzinfo=None）なら UTC 扱いにしてから変換
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(JST).strftime('%Y-%m-%d')

    first_date = to_jst(past5[0].timestamp) if past5 else to_jst(latest.timestamp)
    last_date  = to_jst(latest.timestamp)

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
    if not check_can_use_premium(current_user):
        flash("⚠️ 無料期間は終了しました。有料登録後にご利用ください。")
        return redirect(url_for('dashboard'))
    return render_template('record.html')

@app.route('/api/record')
@login_required
def record_api():  # ← こちらも別名にしておくと安心
    return jsonify({"status": "ok"})

from flask import jsonify

@app.route('/api/upload', methods=['POST'])
@login_required
def upload():
    if 'audio_data' not in request.files:
        return jsonify({'error': '音声データが見つかりません'}), 400

    file = request.files['audio_data']
    if file.filename == '':
        return jsonify({'error': 'ファイルが選択されていません'}), 400

    UPLOAD_FOLDER = '/tmp/uploads'
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    now_jst = datetime.now(JST)
    today_jst = now_jst.date()
    now = now_jst

    original_ext = file.filename.split('.')[-1]
    filename = f"user{current_user.id}_{now.strftime('%Y%m%d_%H%M%S')}.{original_ext}"
    save_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(save_path)

    upload_to_s3(save_path, filename)

    try:
        file_size = os.path.getsize(save_path)
        print(f"📥 アップロードされたファイル: {save_path}")
        print(f"📏 ファイルサイズ: {file_size} バイト")
        if file_size < 5000:
            print("⚠️ ファイルサイズが小さすぎます（録音失敗の可能性）")
    except Exception as e:
        print("❌ ファイルサイズ確認エラー:", e)

    try:
        # 音声変換
        wav_path = save_path.replace(f".{original_ext}", ".wav")
        convert_success = False
        if original_ext.lower() == "m4a":
            convert_success = convert_m4a_to_wav(save_path, wav_path)
        elif original_ext.lower() == "webm":
            convert_success = convert_webm_to_wav(save_path, wav_path)
        else:
            return jsonify({'error': '対応していないファイル形式です'}), 400

        if not convert_success or not is_valid_wav(wav_path):
            return jsonify({'error': '録音が短すぎるか、変換に失敗しました。'}), 400

        raw_debug_path = os.path.join(os.path.dirname(__file__), 'uploads/raw', os.path.basename(wav_path))
        os.makedirs(os.path.dirname(raw_debug_path), exist_ok=True)
        shutil.copy(wav_path, raw_debug_path)

        normalized_filename = os.path.basename(wav_path).replace(".wav", "_normalized.wav")
        normalized_path = os.path.join("/tmp", normalized_filename)
        normalize_volume(wav_path, normalized_path)

        # light_analyze処理
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
        baseline_rms = (sum(log.volume_std for log in recent) / len(recent)) if recent else raw_rms

        quick_score, is_fallback = light_analyze(
            wav_path,
            raw_rms=raw_rms,
            rms_baseline=baseline_rms
        )

    except Exception as e:
        print("❌ 音声処理エラー:", e)
        import traceback
        traceback.print_exc()
        return jsonify({'error': '音声処理に失敗しました'}), 500

    # 1) 当日分の既存レコードを検索（JSTに変換してから日付比較）
    existing = (
        ScoreLog.query
        .filter_by(user_id=current_user.id)
        .filter(
            cast(func.timezone('Asia/Tokyo', ScoreLog.timestamp), Date)
            == today_jst
        )
        .first()
    )

    # 2) overwrite フラグチェック
    overwrite = request.args.get('overwrite') == 'true'
    if existing and not overwrite:
        return jsonify({
            'success': False,
            'already': True,
            'message': '本日はすでにスコアを記録済みです。再録音して上書きする場合は OK を押してください。'
        }), 200

    # 3) 上書きなら既存レコードを削除
    if existing and overwrite:
        db.session.delete(existing)
        db.session.commit()

    # 4) （新規／上書きともに）1回だけログを作成
    log = ScoreLog(
        user_id=current_user.id,
        timestamp=now,               # 先に作った now_jst
        score=quick_score,
        is_fallback=True,
        filename=normalized_filename,
        volume_std=raw_rms,
    )
    db.session.add(log)
    db.session.commit()

    # 5) 永続化→S3→RQ enqueue
    persistent_path = os.path.join(
        os.path.dirname(__file__),
        'uploads',
        os.path.basename(normalized_path)
    )
    os.makedirs(os.path.dirname(persistent_path), exist_ok=True)
    shutil.copy(normalized_path, persistent_path)
    upload_to_s3(normalized_path, os.path.basename(normalized_path))

    job_id = enqueue_detailed_analysis(os.path.basename(normalized_path), current_user.id)
    add_action_log(current_user.id, "録音アップロード（light）")

    return jsonify({
        'quick_score': quick_score,
        'job_id': job_id,
        'success': True
    }), 200

@app.route('/result')
@login_required
def result():
    if not check_can_use_premium(current_user):
        flash("⚠️ 無料期間は終了しました。有料登録後にご利用ください。")
        return redirect(url_for('dashboard'))
    range_type = request.args.get('range', 'all')
    today = date.today()

    if range_type == 'week':
        start_date = today - timedelta(days=7)
        logs = ScoreLog.query.filter(
            ScoreLog.user_id == current_user.id,
            ScoreLog.timestamp >= start_date
        ).order_by(ScoreLog.timestamp).all()
    elif range_type == 'month':
        start_date = today.replace(day=1)
        logs = ScoreLog.query.filter(
            ScoreLog.user_id == current_user.id,
            ScoreLog.timestamp >= start_date
        ).order_by(ScoreLog.timestamp).all()
    else:
        logs = ScoreLog.query.filter_by(user_id=current_user.id).order_by(ScoreLog.timestamp).all()

    dates = [log.timestamp.strftime('%m/%d') for log in logs]
    scores = [log.score for log in logs]

    # ✅ 最初の5回分のスコアの平均（ベースライン）
    first_five_scores = scores[:5]
    baseline = round(sum(first_five_scores) / len(first_five_scores), 2) if first_five_scores else 0

    return render_template('result.html', dates=dates, scores=scores, first_score=scores[0] if scores else 0, baseline=baseline)

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
            "created_at": user.created_at.isoformat(),
            "is_paid": user.is_paid,
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
                'created_at': user.created_at,
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
    
@app.route('/music')
@login_required
def music():
    if not check_can_use_premium(current_user):
        flash("⚠️ 無料期間は終了しました。有料登録後にご利用ください。")
        return redirect(url_for('dashboard'))
    
    # 無料期間 or 有料 or 拡張フラグ
    can_play_premium = check_can_use_premium(current_user)

    filenames = sorted(os.path.basename(f) for f in glob.glob("static/paid/*.mp3"))
    display_names = {
        "positive1.mp3": "サウンドトラック 01",
        "positive2.mp3": "サウンドトラック 02",
        "positive3.mp3": "サウンドトラック 03",
        "positive4.mp3": "サウンドトラック 04",
        "positive5.mp3": "サウンドトラック 05",
        "relax1.mp3": "サウンドトラック 01",
        "relax2.mp3": "サウンドトラック 02",
        "relax3.mp3": "サウンドトラック 03",
        "relax4.mp3": "サウンドトラック 04",
        "relax5.mp3": "サウンドトラック 05",
        "mindfulness1.mp3": "サウンドトラック 01",
        "mindfulness2.mp3": "サウンドトラック 02",
        "mindfulness3.mp3": "サウンドトラック 03",
        "mindfulness4.mp3": "サウンドトラック 04",
        "mindfulness5.mp3": "サウンドトラック 05",
    }

    tracks = [{"filename": f, "display": display_names.get(f, f)} for f in filenames]

    return render_template('unified_music.html', tracks=tracks, can_play_premium=can_play_premium)

@app.route('/api/music')
@login_required
def api_music():
    if not check_can_use_premium(current_user):
        return jsonify({
            'error': '無料期間が終了しています。有料登録が必要です。'
        }), 403

    # …以降はそのまま

    filenames = sorted(os.path.basename(f) for f in glob.glob("static/paid/*.mp3"))

    display_names = {
        "positive1.mp3": "サウンドトラック 01",
        "positive2.mp3": "サウンドトラック 02",
        "positive3.mp3": "サウンドトラック 03",
        "positive4.mp3": "サウンドトラック 04",
        "positive5.mp3": "サウンドトラック 05",
        "relax1.mp3": "サウンドトラック 01",
        "relax2.mp3": "サウンドトラック 02",
        "relax3.mp3": "サウンドトラック 03",
        "relax4.mp3": "サウンドトラック 04",
        "relax5.mp3": "サウンドトラック 05",
        "mindfulness1.mp3": "サウンドトラック 01",
        "mindfulness2.mp3": "サウンドトラック 02",
        "mindfulness3.mp3": "サウンドトラック 03",
        "mindfulness4.mp3": "サウンドトラック 04",
        "mindfulness5.mp3": "サウンドトラック 05",
    }

    tracks = [
        {
            "filename": f,
            "display": display_names.get(f, f),
            "url": f"/static/paid/{f}"
        }
        for f in filenames
    ]

    return jsonify({
        "tracks": tracks
    })

@app.route('/diary')
@login_required
def diary_redirect():
    return render_template('diary.html')

@app.route('/checkout')
@login_required
def checkout():
    return render_template('checkout.html')

@app.route('/create-checkout-session', methods=['POST'])
@login_required
def create_checkout_session():
    try:
        stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
        price_id = os.getenv("STRIPE_PRICE_ID")
        print(f"STRIPE_SECRET_KEY={stripe.api_key[:10]}..., PRICE_ID={price_id}")
        print(f"current_user.email={current_user.email}")

        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=url_for('dashboard', _external=True),
            cancel_url=url_for('dashboard', _external=True),
            customer_email=current_user.email
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        print(f"[CHECKOUT ERROR]: {e}")
        return str(e), 400

@app.route("/webhook", methods=["POST"])
def stripe_webhook():
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
    print("📩 Webhook受信しました")
    print(f"Content-Type: {request.headers.get('Content-Type')}")
    print(f"Stripe-Signature: {request.headers.get('Stripe-Signature')}")

    content_type = request.headers.get("Content-Type", "")
    if not content_type.startswith("application/json"):
        return "Unsupported Media Type", 415

    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")

    # ✅ 環境に応じてWebhookシークレットを切り替える！
    if os.getenv("FLASK_ENV") == "development":
        endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET_TEST")
    else:
        endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except ValueError:
        print("❌ Invalid payload")
        return "Invalid payload", 400
    except stripe.error.SignatureVerificationError as e:
        print("❌ Invalid signature")
        print(f"詳細: {str(e)}")
        return "Invalid signature", 400

    print(f"✅ Event type: {event['type']}")

    if event["type"] == "checkout.session.completed":
        session_data = event["data"]["object"]
        email = session_data.get("customer_email")
    
        # 🔍 customer_emailがない場合はcustomerから取得
        if not email and session_data.get("customer"):
            try:
                customer = stripe.Customer.retrieve(session_data["customer"])
                email = customer.get("email")
                print(f"📧 Stripe顧客情報から取得したメール: {email}")
            except Exception as e:
                print(f"❌ 顧客情報の取得に失敗: {e}")

        if email:
            print(f"🎯 顧客メール: {email}")
            user = User.query.filter_by(email=email).first()
            if user:
                print(f"✅ ユーザー {email} が見つかりました。is_paidを更新します")
                user.is_paid = True
                user.has_ever_paid = True
                db.session.commit()
                print(f"💰 {email} の支払いステータスを更新しました")
            else:
                print(f"❌ ユーザー {email} が見つかりませんでした")
        else:
            print("❌ 顧客メールが取得できませんでした")

        return jsonify(success=True)

    # ---------------------------
    # 2. サブスク解約（is_paid=False）
    elif event["type"] == "customer.subscription.deleted":
        subscription = event["data"]["object"]
        customer_id = subscription.get("customer")
        print(f"🗑 サブスク削除検知 customer_id={customer_id}")

        # Stripe顧客IDからユーザー特定
        try:
            customer = stripe.Customer.retrieve(customer_id)
            email = customer.get("email")
            print(f"📧 顧客メール: {email}")
        except Exception as e:
            print(f"❌ 顧客情報取得失敗: {e}")
            email = None

        if email:
            user = User.query.filter_by(email=email).first()
            if user:
                user.is_paid = False
                user.has_ever_paid = True
                db.session.commit()
                print(f"✅ ユーザー {email} のis_paidをFalseにしました")
            else:
                print("❌ DBに該当ユーザーがいません")
        else:
            print("❌ 顧客メール取得できず")

        return jsonify(success=True)

    # ---------------------------
    # その他イベント
    else:
        return jsonify(success=True)

# ✅ 無制限メールアドレスリスト（漏洩リスクに備えて限定的に）
ALLOWED_FREE_EMAILS = ['ta714kadvance@gmail.com']

@app.route('/api/profile')
def api_profile():
    if not current_user.is_authenticated:
        return jsonify({
            'error': '未ログイン状態です',
            'email': None,
            'is_paid': False,
            'is_free_extended': False,
            'created_at': None
        }), 401

    can_use_premium = check_can_use_premium(current_user)

    today = date.today()
    today_score = (
        ScoreLog.query
        .filter_by(user_id=current_user.id)
        .filter(db.func.date(ScoreLog.timestamp) == today)
        .order_by(ScoreLog.timestamp.desc())
        .first()
    )
    today_score_value = today_score.score if today_score else None

    last_log = (
        ScoreLog.query
        .filter_by(user_id=current_user.id)
        .order_by(ScoreLog.timestamp.desc())
        .first()
    )
    last_recorded = last_log.timestamp.strftime('%Y-%m-%d %H:%M:%S') if last_log else None

    last_5_logs = (
        ScoreLog.query
        .filter_by(user_id=current_user.id)
        .order_by(ScoreLog.timestamp.asc())
        .limit(5)
        .all()
    )
    baseline = round(sum(log.score for log in last_5_logs) / len(last_5_logs), 1) if last_5_logs else None

    score_deviation = round(today_score_value - baseline, 1) if today_score_value and baseline else None

    return jsonify({
        'email': current_user.email,
        'username': current_user.username,
        'birthdate': current_user.birthdate,
        'gender': current_user.gender,
        'occupation': current_user.occupation,
        'prefecture': current_user.prefecture,
        'created_at': current_user.created_at.isoformat() if current_user.created_at else None,
        'last_score': today_score_value,
        'last_recorded': last_recorded,
        'baseline': baseline,
        'score_deviation': score_deviation,
        'is_paid': current_user.is_paid,
        'is_free_extended': current_user.is_free_extended,
        'can_use_premium': can_use_premium
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
    range_type = request.args.get('range', 'all')
    today = date.today()

    if range_type == 'week':
        start_date = today - timedelta(days=7)
        logs = ScoreLog.query.filter(
            ScoreLog.user_id == current_user.id,
            ScoreLog.timestamp >= start_date
        ).order_by(ScoreLog.timestamp).all()
    elif range_type == 'month':
        start_date = today.replace(day=1)
        logs = ScoreLog.query.filter(
            ScoreLog.user_id == current_user.id,
            ScoreLog.timestamp >= start_date
        ).order_by(ScoreLog.timestamp).all()
    else:
        logs = ScoreLog.query.filter_by(user_id=current_user.id).order_by(ScoreLog.timestamp).all()

    scores = [{
        'date': log.timestamp.strftime('%Y-%m-%d'),
        'score': log.score,
        'is_fallback': log.is_fallback
    } for log in logs]

    valid_logs = [log for log in logs if not log.is_fallback]
    if valid_logs:
        baseline = sum(log.score for log in valid_logs[:5]) / min(len(valid_logs), 5)
        latest_score = valid_logs[-1].score
        diff = round(latest_score - baseline, 1)
    else:
        baseline = 0
        latest_score = 0
        diff = 0

    return jsonify({
        'scores': scores,
        'baseline': round(baseline, 1),
        'latest': latest_score,
        'diff': diff
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

# ✅ ローカル起動用（Renderでは無視される）
if __name__ == '__main__':
    app.run(debug=True)
