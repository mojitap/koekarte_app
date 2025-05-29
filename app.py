# 完全修正版 app.py
# ✅ DBのみを使用、ScoreLogで記録管理、管理者ページ対応済み

import os, time, glob, wave, csv, joblib
import numpy as np
import stripe
import python_speech_features
import librosa
from datetime import datetime, date, timedelta, timezone
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from dotenv import load_dotenv
from io import StringIO
from scipy.signal import butter, lfilter
from pydub import AudioSegment
from pyAudioAnalysis import audioBasicIO, MidTermFeatures
from models import db, User, ScoreLog
from flask_migrate import Migrate

# ✅ Flaskアプリ作成
app = Flask(__name__)
load_dotenv()

# ✅ 設定読み込み
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///db.sqlite3'  # ← ローカル用
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = os.getenv('SECRET_KEY')

# ✅ DBとアプリを紐付け
db.init_app(app)
migrate = Migrate(app, db)

# ✅ LoginManagerなど初期化（必要なら）
login_manager = LoginManager()
login_manager.init_app(app)

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

# CORS設定（セッション対応）
CORS(app, origins=[
    "http://localhost:19006",         # ← Expo GoのWebプレビュー
    "http://192.168.0.16:19006",      # ← ローカルWi-Fi経由のExpoアプリ
], supports_credentials=True)

app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = True

login_manager = LoginManager()
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

def convert_webm_to_wav(webm_path, wav_path):
    try:
        audio = AudioSegment.from_file(webm_path, format="webm")
        print(f"🔍 WebM録音長さ（秒）: {audio.duration_seconds}")
        
        # ⬇ PCM 16bitで保存（これが重要！）
        audio.export(wav_path, format="wav", parameters=["-acodec", "pcm_s16le"])

        with wave.open(wav_path, 'rb') as wf:
            frames = wf.getnframes()
            framerate = wf.getframerate()
            duration = frames / float(framerate)
            print(f"🔍 WAVファイルの長さ: {duration:.2f} 秒, フレーム数: {frames}")

            if frames == 0 or duration < 1.0:
                raise ValueError("生成されたWAVファイルが無効です（録音が短すぎるか空）")
    except Exception as e:
        print("❌ WebM→WAV変換エラー:", e)
        import traceback
        traceback.print_exc()
        raise

def is_valid_wav(wav_path):
    try:
        with wave.open(wav_path, 'rb') as wf:
            frames = wf.getnframes()
            duration = frames / wf.getframerate()
            return duration > 1.0
    except Exception:
        return False

def bandpass_filter(signal, rate, lowcut=300, highcut=3400, order=5):
    nyquist = 0.5 * rate
    low = lowcut / nyquist
    high = highcut / nyquist
    b, a = butter(order, [low, high], btype='band')
    return lfilter(b, a, signal)
    
def analyze_stress_from_wav(wav_path):
    [sampling_rate, signal] = audioBasicIO.read_audio_file(wav_path)
    signal = np.asarray(signal).flatten()

    if signal.dtype != np.float32:
        signal = signal.astype(np.float32)

    max_abs = np.max(np.abs(signal))
    if max_abs > 0:
        signal = signal / max_abs

    signal = bandpass_filter(signal, sampling_rate)

    if len(signal) == 0:
        raise ValueError("Empty audio file")

    duration_sec = len(signal) / sampling_rate
    if duration_sec < 5:
        raise ValueError("録音が短すぎます（最低5秒以上必要）")

    mt_win = min(2.0, duration_sec / 3)
    mt_step = mt_win / 2
    st_win, st_step = 0.05, 0.025

    try:
        mt_feats, _, _ = MidTermFeatures.mid_feature_extraction(
            signal, sampling_rate, mt_win, mt_step, st_win, st_step
        )
        if mt_feats.shape[1] == 0:
            raise ValueError("抽出された特徴量が空です")

        feature_means = np.mean(mt_feats, axis=1)
        zcr = feature_means[0]
        energy = feature_means[1]
        entropy = feature_means[2]

        # --- 追加特徴量 ---
        pitches, magnitudes = librosa.piptrack(y=signal, sr=sampling_rate)
        pitch_values = pitches[magnitudes > np.median(magnitudes)]
        pitch_mean = np.mean(pitch_values) if len(pitch_values) > 0 else 0
        pitch_var = np.var(pitch_values) if len(pitch_values) > 0 else 0

        zcr_rate = np.mean(librosa.feature.zero_crossing_rate(y=signal))

        intervals = librosa.effects.split(signal, top_db=40)
        voiced_duration = sum((e - s) for s, e in intervals)
        total_duration = len(signal)
        pause_ratio = 1.0 - (voiced_duration / total_duration)

        mfccs = librosa.feature.mfcc(y=signal, sr=sampling_rate, n_mfcc=13)
        mfcc_mean = np.mean(mfccs, axis=1)

        all_features = [zcr, energy, entropy, pitch_mean, pitch_var, zcr_rate, pause_ratio] + list(mfcc_mean)

        model = joblib.load("light_model.pkl")
        
        # モデルからスコアを取得
        score = model.predict([all_features])[0]

        # 小声補正（energyベース）
        raw_energy = np.mean(signal ** 2)
        if raw_energy < 0.001:
            score += 5
        elif raw_energy < 0.005:
            score += 3
        elif raw_energy > 0.05:
            score -= 3

        # ✅ スパム的な極端なスコア制限
        score = max(15, min(int(score), 85))

        # ✅ ベースラインとの比較補正
        from flask_login import current_user
        recent_logs = (
            ScoreLog.query
            .filter_by(user_id=current_user.id)
            .order_by(ScoreLog.timestamp.asc())  # ✅ 昇順で「登録初期」から取得
            .limit(5)
            .all()
        )

        if recent_logs and len(recent_logs) >= 5:
            baseline = sum(log.score for log in recent_logs) / len(recent_logs)
            deviation = score - baseline
            # ±30点以上の差が出たら、30点以内に丸める
            if deviation > 30:
                score = int(baseline + 30)
            elif deviation < -30:
                score = int(baseline - 30)

        # 最終的なスコアを 0〜100 に収める
        return max(0, min(score, 100))

    except Exception as e:
        print("❌ 特徴量抽出失敗（代替スコア使用）:", e)
        energy = np.mean(signal ** 2)
        return min(100, max(0, int(energy * 1e4)))

# ======== メール送信 =========
def send_confirmation_email(user_email, username):
    token = serializer.dumps(user_email, salt='email-confirm')
    confirm_url = url_for('confirm_email', token=token, _external=True, _scheme='https')
    confirm_url = confirm_url.replace("localhost:5000", "koekarte.com")

    msg = Message('【koekarte】ご登録ありがとうございます',
                  sender='noreply@koekarte.com',  # ✅ 明示
                  recipients=[user_email])
    msg.body = f"""{username} 様\n\n以下のリンクをクリックして本登録を完了してください：\n{confirm_url}\n\nこのリンクは一定時間で無効になります。\n\n-- koekarte 運営"""
    mail.send(msg)

# ======== ルート定義 =========
@app.route('/send-test-mail')
def send_test_mail():
    msg = Message(subject="テスト送信",
                  recipients=["ta714kadvance@gmail.com"],
                  body="MailerSendのSMTP経由で送信されたテストメールです。")
    mail.send(msg)
    return "メールを送信しました！"

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        message = request.form['message']

        msg = Message(subject="【koekarte】お問い合わせ",
                      sender='noreply@koekarte.com',
                      recipients=['koekarte.info@gmail.com'])
        msg.body = f"""
【お問い合わせ】
名前: {name}
メール: {email}

内容:
{message}
"""
        mail.send(msg)
        flash("お問い合わせを送信しました。ありがとうございます。")
        return redirect(url_for('contact'))

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
        birthdate = request.form.get('birthdate')
        gender = request.form.get('gender')
        occupation = request.form.get('occupation')
        prefecture = request.form.get('prefecture')

        # ✅ 既にメール or ユーザー名が使われていたら弾く
        if User.query.filter_by(email=email).first():
            flash('このメールアドレスは既に登録されています。')
            return redirect(url_for('register'))

        user = User(
            username=username, email=email, password=password,
            birthdate=birthdate, gender=gender,
            occupation=occupation, prefecture=prefecture
        )
        db.session.add(user)
        db.session.commit()
        send_confirmation_email(email, username)
        return '確認メールを送信しました'
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        identifier = request.form['username']
        password = request.form['password']
        user = User.query.filter((User.username == identifier) | (User.email == identifier)).first()
        if not user or not check_password_hash(user.password, password):
            return 'ログイン失敗'
        if not user.is_verified:
            return 'メール確認が必要です'

        login_user(user)

        # ✅ セッションを30日間持続させるために追加
        session.permanent = True

        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/export_csv')
@login_required
def export_csv():
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

# --- パスワード再設定メール送信 ---
def send_reset_email(user):
    token = serializer.dumps(user.email, salt='reset-password')
    reset_url = url_for('reset_password', token=token, _external=True, _scheme='https')

    msg = Message('【koekarte】パスワード再設定リンク',
                  sender='noreply@koekarte.com',  # ✅ 明示
                  recipients=[user.email])
    msg.body = f"""
{user.username} 様

以下のリンクよりパスワードの再設定を行ってください：
{reset_url}

このリンクは1時間で無効になります。
"""
    mail.send(msg)

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

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/dashboard')
@login_required
def dashboard():
    if not current_user.is_verified:
        flash("メールアドレスの確認が完了していません。")
        return redirect(url_for('home'))

    logs = ScoreLog.query.filter_by(user_id=current_user.id).order_by(ScoreLog.timestamp).all()

    baseline = None
    first_score = None
    latest_score = None
    diff = None
    first_score_date = None
    last_date = None

    if logs:
        scores = [log.score for log in logs]
        dates = [log.timestamp.strftime('%Y-%m-%d') for log in logs]

        first_score = logs[0].score
        latest_score = logs[-1].score
        first_score_date = dates[0]
        last_date = dates[-1]

        # ✅ 最初の5回分のスコアの平均をベースラインに
        if len(scores) >= 5:
            baseline = sum(scores[:5]) // 5

        else:
            baseline = sum(scores) // len(scores)

        # ✅ ベースラインとの差分を計算（順番ここ！）
        diff = latest_score - baseline

    return render_template('dashboard.html',
                           user=current_user,
                           first_score=first_score,
                           latest_score=latest_score,
                           diff=diff,
                           first_score_date=first_score_date,
                           last_date=last_date,
                           baseline=baseline)

@app.route('/api/forgot-password', methods=['POST'])
def api_forgot_password():
    data = request.get_json()
    email = data.get('email')

    if not email:
        return jsonify({'error': 'メールアドレスが必要です'}), 400

    user = User.query.filter_by(email=email).first()
    if user:
        send_reset_email(user)  # ✅ これは既存の関数を呼び出すだけ！

    return jsonify({'message': '再設定メールを送信しました（存在する場合）'})
    

@app.route('/record')
@login_required
def record():
    return render_template('record.html')

@app.route('/upload', methods=['POST'])
@login_required
def upload():
    if 'audio_data' not in request.files:
        print("❌ audio_data が見つかりません")
        return '音声データが見つかりません'

    file = request.files['audio_data']
    if file.filename == '':
        print("❌ ファイル名が空です")
        return 'ファイルが選択されていません'

    UPLOAD_FOLDER = 'uploads'
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    today = now.date()

    # 🔽 元のwebmファイルと、変換後のwavファイルのパスを準備
    webm_path = os.path.join(UPLOAD_FOLDER, f"user{current_user.id}_{now.strftime('%Y%m%d_%H%M%S')}.webm")
    wav_path = webm_path.replace('.webm', '.wav')

    file.save(webm_path)
    print(f"✅ WebMファイル保存完了: {webm_path}")

    try:
        convert_webm_to_wav(webm_path, wav_path)
        print(f"✅ WAVファイルへ変換成功: {wav_path}")
    except Exception as e:
        print("❌ WebM→WAV変換エラー:", e)
        return '音声変換に失敗しました'

    if not is_valid_wav(wav_path):
        print("❌ WAVファイルが無効 or 長さ不足")
        return '録音が短すぎます。もう一度お試しください。'

    try:
        stress_score = analyze_stress_from_wav(wav_path)
        print(f"✅ 分析完了: ストレススコア = {stress_score}")
    except Exception as e:
        print("❌ 分析処理エラー:", e)
        return '音声分析に失敗しました'

    existing = ScoreLog.query.filter_by(user_id=current_user.id).filter(db.func.date(ScoreLog.timestamp) == today).first()
    if existing:
        print("⚠️ すでに今日のデータが存在します")
        return '本日はすでに保存済みです（1日1回制限）'

    try:
        new_log = ScoreLog(user_id=current_user.id, timestamp=now, score=stress_score)
        db.session.add(new_log)
        db.session.commit()
        print("✅ スコア保存成功")
    except Exception as e:
        print("❌ DB保存失敗:", e)
        return 'データベース保存失敗'

    return redirect(url_for('dashboard'))

@app.route('/result')
@login_required
def result():
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
        current_user.username = request.form['username']
        current_user.birthdate = request.form['birthdate']
        current_user.gender = request.form['gender']
        current_user.occupation = request.form['occupation']
        current_user.prefecture = request.form['prefecture']
        db.session.commit()
        flash("プロフィールを更新しました")
        return redirect(url_for('dashboard'))

    return render_template('edit_profile.html', user=current_user)
    
@app.route('/api/register', methods=['POST'])
def api_register():
    try:
        data = request.get_json()
        email = data.get('email')
        username = data.get('username')
        password = data.get('password')

        birthdate_str = data.get('birthdate')
        birthdate = datetime.strptime(birthdate_str, '%Y-%m-%d').date() if birthdate_str else None

        gender = data.get('gender')
        occupation = data.get('occupation')
        prefecture = data.get('prefecture')

        if not email or not username or not password:
            return jsonify({'error': 'メール・名前・パスワードは必須です'}), 400

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            return jsonify({'error': 'このメールアドレスは既に使われています'}), 400

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

        return jsonify({
            "email": user.email,
            "created_at": user.created_at.isoformat(),
            "is_paid": user.is_paid,
            "is_free_extended": bool(user.is_free_extended),
        })
    except Exception as e:
        print("❌ 登録エラー:", e)
        return jsonify({'error': '登録中にエラーが発生しました'}), 500
    
@app.route('/api/login', methods=['POST'])
def api_login():
    try:
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
        current_user.email = data.get('email', current_user.email)
        current_user.username = data.get('username', current_user.username)

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
        return jsonify({'message': '✅ 通常プロフィール更新成功'})
    except Exception as e:
        return jsonify({'error': f'プロフィール更新エラー: {str(e)}'}), 400
    
@app.route('/music/free')
def free_music():
    return render_template('free_music.html')

@app.route('/music/premium')
@login_required
def premium_music():
    if not current_user.is_paid:
        flash("プレミアム音源は有料プラン専用です。")
        return redirect(url_for('dashboard'))

    filenames = [os.path.basename(f) for f in glob.glob("static/paid/*.mp3")]

    display_names = {
        "positive1.mp3": "サウンドトラック 01",
        "positive2.mp3": "サウンドトラック 02",
        "positive3.mp3": "サウンドトラック 03",
        "positive4.mp3": "サウンドトラック 04",
        "positive5.mp3": "サウンドトラック 05",
        "relax1.mp3": "サウンドトラック 06",
        "relax2.mp3": "サウンドトラック 07",
        "relax3.mp3": "サウンドトラック 08",
        "relax4.mp3": "サウンドトラック 09",
        "relax5.mp3": "サウンドトラック 10",
        "mindfulness1.mp3": "サウンドトラック 11",
        "mindfulness2.mp3": "サウンドトラック 12",
        "mindfulness3.mp3": "サウンドトラック 13",
        "mindfulness4.mp3": "サウンドトラック 14",
        "mindfulness5.mp3": "サウンドトラック 15",
    }

    tracks = [
        {"filename": f, "display": display_names.get(f, f)}
        for f in filenames
    ]

    return render_template('premium_music.html', tracks=tracks)

@app.route('/checkout')
@login_required
def checkout():
    return render_template('checkout.html')

@app.route('/create-checkout-session', methods=['POST'])
@login_required
def create_checkout_session():
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': os.getenv("STRIPE_PRICE_ID"),  # .env に設定済み
                'quantity': 1,
            }],
            mode='subscription',
            success_url=url_for('dashboard', _external=True),
            cancel_url=url_for('dashboard', _external=True),
            customer_email=current_user.email
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        return str(e), 400

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET")  # .env に追加が必要！

@app.route("/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
    except ValueError:
        return "Invalid payload", 400
    except stripe.error.SignatureVerificationError:
        return "Invalid signature", 400

    # ✅ 支払い完了時に実行
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        email = session.get("customer_email")
        user = User.query.filter_by(email=email).first()
        if user:
            user.is_paid = True
            db.session.commit()
            print(f"✅ {email} を有料プランに更新しました")

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

    # 無料延長判定
    is_free_extended = current_user.is_free_extended or current_user.email in ALLOWED_FREE_EMAILS

    return jsonify({
        'email': current_user.email,
        'username': current_user.username,
        'birthdate': current_user.birthdate,
        'gender': current_user.gender,
        'occupation': current_user.occupation,
        'prefecture': current_user.prefecture,
        'is_paid': current_user.is_paid,
        'is_free_extended': is_free_extended,
        'created_at': current_user.created_at.isoformat()
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
    logs = ScoreLog.query.filter_by(user_id=current_user.id).order_by(ScoreLog.timestamp).all()

    scores = [{
        'date': log.timestamp.strftime('%Y-%m-%d'),
        'score': log.score
    } for log in logs]

    if len(logs) >= 5:
        baseline = sum(log.score for log in logs[:5]) / 5
    else:
        baseline = sum(log.score for log in logs) / len(logs) if logs else 0

    return jsonify({
        'scores': scores,
        'baseline': round(baseline, 1)
    })

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = 'http://192.168.0.16:19006'
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    return response
    
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
    
