# 完全修正版 app.py
# ✅ DBのみを使用、ScoreLogで記録管理、管理者ページ対応済み

from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime, date, timedelta
from pydub import AudioSegment
from pyAudioAnalysis import audioBasicIO, MidTermFeatures
import numpy as np
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer
from dotenv import load_dotenv
import wave

app = Flask(__name__)
load_dotenv()

app.permanent_session_lifetime = timedelta(days=30)
app.secret_key = os.getenv('SECRET_KEY')
serializer = URLSafeTimedSerializer(app.secret_key)

# メール設定
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
mail = Mail(app)

# DB設定
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# モデル定義
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False, unique=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    birthdate = db.Column(db.String(20))
    gender = db.Column(db.String(10))
    occupation = db.Column(db.String(100))
    prefecture = db.Column(db.String(20))
    is_verified = db.Column(db.Boolean, default=False)
    score_logs = db.relationship('ScoreLog', backref='user', lazy=True)

class ScoreLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False)
    score = db.Column(db.Integer, nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ======== 音声処理 =========
def convert_webm_to_wav(webm_path, wav_path):
    audio = AudioSegment.from_file(webm_path, format="webm")
    audio.export(wav_path, format="wav")
    with wave.open(wav_path, 'rb') as wf:
        frames = wf.getnframes()
        framerate = wf.getframerate()
        duration = frames / float(framerate)
        if frames == 0 or duration < 1.0:
            raise ValueError("生成されたWAVファイルが無効です")

def is_valid_wav(wav_path):
    try:
        with wave.open(wav_path, 'rb') as wf:
            frames = wf.getnframes()
            duration = frames / wf.getframerate()
            return duration > 1.0
    except Exception:
        return False

def analyze_stress_from_wav(wav_path):
    [sampling_rate, signal] = audioBasicIO.read_audio_file(wav_path)
    if len(signal) == 0:
        raise ValueError("Empty audio file")
    mt_feats, _, _ = MidTermFeatures.mid_feature_extraction(
        signal, sampling_rate, 2.0, 1.0, 0.05, 0.025
    )
    if mt_feats.shape[1] == 0:
        raise ValueError("No features extracted")
    feature_means = np.mean(mt_feats, axis=1)
    energy = feature_means[1]
    zero_crossing_rate = feature_means[0]
    score = int((energy + zero_crossing_rate) * 50)
    return max(0, min(score, 100))

# ======== メール送信 =========
def send_confirmation_email(user_email, username):
    token = serializer.dumps(user_email, salt='email-confirm')
    confirm_url = url_for('confirm_email', token=token, _external=True, _scheme='https')
    confirm_url = confirm_url.replace("localhost:5000", "koekarte.com")
    msg = Message('【koekarte】ご登録ありがとうございます',
                  sender=os.getenv('MAIL_USERNAME'),
                  recipients=[user_email])
    msg.body = f"""{username} 様\n\n以下のリンクをクリックして本登録を完了してください：\n{confirm_url}\n\nこのリンクは一定時間で無効になります。\n\n-- koekarte 運営"""
    mail.send(msg)

# ======== ルート定義 =========
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
        if User.query.filter_by(email=email).first():
            return '既に登録されています'
        user = User(username=username, email=email, password=password,
                    birthdate=birthdate, gender=gender, occupation=occupation, prefecture=prefecture)
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
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/dashboard')
@login_required
def dashboard():
    logs = ScoreLog.query.filter_by(user_id=current_user.id).order_by(ScoreLog.timestamp).all()
    first_score = logs[0].score if logs else None
    latest_score = logs[-1].score if logs else None
    diff = (latest_score - first_score) if (first_score is not None and latest_score is not None) else None
    first_score_date = logs[0].timestamp.strftime('%Y-%m-%d') if logs else None
    return render_template('dashboard.html', user=current_user, first_score=first_score, latest_score=latest_score, diff=diff, first_score_date=first_score_date)

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

    now = datetime.now()
    today = date.today()
    filename = f"user{current_user.id}_{now.strftime('%Y%m%d_%H%M%S')}.wav"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)
    print(f"✅ ファイル保存: {filepath}")

    existing = ScoreLog.query.filter_by(user_id=current_user.id).filter(db.func.date(ScoreLog.timestamp) == today).first()
    if existing:
        print("⚠️ 本日すでに記録あり。保存スキップ。")
        return '本日はすでに保存済みです（1日1回制限）'

    if not is_valid_wav(filepath):
        print("❌ WAVファイルが無効")
        flash("録音に失敗しました。もう一度お試しください。")
        return redirect(url_for("record"))

    try:
        stress_score = analyze_stress_from_wav(filepath)
        print(f"✅ 分析結果: ストレススコア = {stress_score}")
    except Exception as e:
        print("❌ 分析エラー:", e)
        return 'ストレス分析に失敗しました'

    try:
        new_log = ScoreLog(user_id=current_user.id, timestamp=now, score=stress_score)
        db.session.add(new_log)
        db.session.commit()
        print("✅ スコア保存完了")
    except Exception as e:
        print("❌ データベース保存失敗:", e)
        return 'データベース保存失敗'

    return redirect(url_for("dashboard"))

@app.route('/result')
@login_required
def result():
    logs = ScoreLog.query.filter_by(user_id=current_user.id).order_by(ScoreLog.timestamp).all()
    dates = [log.timestamp.strftime('%Y-%m-%d %H:%M:%S') for log in logs]
    scores = [log.score for log in logs]
    return render_template('result.html', dates=dates, scores=scores)

@app.route('/admin')
@login_required
def admin():
    if current_user.email != 'koekarte.info@gmail.com':
        return 'アクセス権がありません', 403

    users = User.query.all()
    for user in users:
        user.score_logs = ScoreLog.query.filter_by(user_id=user.id).order_by(ScoreLog.timestamp).all()
    return render_template('admin.html', users=users)

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/legal')
def legal():
    return render_template('legal.html')

try:
    with app.app_context():
        db.create_all()
except Exception as e:
    print("❌ データベース接続に失敗しました:", e)
