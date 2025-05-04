from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime
import random
import csv
from pydub import AudioSegment
from pyAudioAnalysis import audioBasicIO, MidTermFeatures
import numpy as np
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer
from dotenv import load_dotenv
from datetime import timedelta
import wave

app = Flask(__name__)
load_dotenv()

# ✅ セッションを30日間維持
app.permanent_session_lifetime = timedelta(days=30)

# ✅ SECRET_KEY を先に設定
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
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')  # ← PostgreSQLを使用
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

with app.app_context():
    db.create_all()

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# ユーザーモデル
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

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def convert_webm_to_wav(webm_path, wav_path):
    try:
        audio = AudioSegment.from_file(webm_path, format="webm")
        audio.export(wav_path, format="wav")
        print("🔍 変換された wav の長さ（秒）:", audio.duration_seconds)
    except Exception as e:
        print("❌ WebM→WAV変換に失敗:", e)
        raise RuntimeError("録音ファイルの変換に失敗しました。形式や音声を確認してください。")

    # ✅ WAVファイルの中身が空でないかをチェック
    try:
        with wave.open(wav_path, 'rb') as wf:
            frames = wf.getnframes()
            framerate = wf.getframerate()
            duration = frames / float(framerate)
            print("📏 WAVフレーム数:", frames)
            print("📏 WAV秒数（計算）:", duration)

            if frames == 0 or duration < 1.0:
                raise ValueError("生成されたWAVファイルが無効です（無音または破損）")

    except Exception as e:
        print("❌ WAVファイルチェックでエラー:", e)
        raise RuntimeError("WAVファイルが壊れている可能性があります")
       
def is_valid_wav(wav_path):
    try:
        with wave.open(wav_path, 'rb') as wf:
            frames = wf.getnframes()
            duration = frames / wf.getframerate()
            return duration > 1.0
    except Exception:
        return False
         
def analyze_stress_from_wav(wav_path):
    try:
        [sampling_rate, signal] = audioBasicIO.read_audio_file(wav_path)

        if len(signal) == 0:
            print("🔴 エラー：wavファイルが空です")
            raise ValueError("Empty audio file")

        mt_feats, _, _ = MidTermFeatures.mid_feature_extraction(
            signal, sampling_rate, 2.0, 1.0, 0.05, 0.025
        )

        if mt_feats.shape[1] == 0:
            print("⚠️ 特徴量が抽出できませんでした（無音の可能性）")
            raise ValueError("No features extracted (possibly silent audio)")

        feature_means = np.mean(mt_feats, axis=1)
        energy = feature_means[1]
        zero_crossing_rate = feature_means[0]
        score = int((energy + zero_crossing_rate) * 50)
        return max(0, min(score, 100))

    except Exception as e:
        print(f"⚠️ ストレス分析中にエラー: {e}")
        raise RuntimeError("ストレス分析に失敗しました。音声を確認してください。")

def send_confirmation_email(user_email, username):
    token = serializer.dumps(user_email, salt='email-confirm')
    confirm_url = url_for('confirm_email', token=token, _external=True, _scheme='https')
    confirm_url = confirm_url.replace("localhost:5000", "koekarte.com")
    msg = Message('【koekarte】ご登録ありがとうございます',
                  sender=os.getenv('MAIL_USERNAME'),
                  recipients=[user_email])
    msg.body = f"""{username} 様

このたびは、音声ストレスチェックサービス「koekarte（コエカルテ）」にご登録いただき、誠にありがとうございます。

本メールは、ご登録の確認のためにお送りしております。
以下のリンクをクリックして、本登録を完了してください：
{confirm_url}

このリンクは一定時間で無効になります。

────────────────────
koekarte（コエカルテ）運営事務局
https://koekarte.com
メール：{os.getenv('MAIL_USERNAME')}
"""
    mail.send(msg)

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/confirm/<token>')
def confirm_email(token):
    try:
        email = serializer.loads(token, salt='email-confirm', max_age=3600)
    except:
        return render_template('confirm_failed.html')  # 既にOK

    user = User.query.filter_by(email=email).first_or_404()
    if user.is_verified:
        return redirect(url_for('login'))  # 既に確認済みならログインへ

    user.is_verified = True
    db.session.commit()
    return "<h1>✅ メールアドレスが確認されました！</h1><p><a href='/login'>ログインへ戻る</a></p>"

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password_raw = request.form.get('password', '')
        birth_year = request.form.get('birth_year')
        birth_month = request.form.get('birth_month')
        birth_day = request.form.get('birth_day')
        gender = request.form.get('gender')
        occupation = request.form.get('occupation', '')
        prefecture = request.form.get('prefecture')

        # 入力チェック（最小限）
        if not username or not email or not password_raw or not birth_year or not birth_month or not birth_day:
            return '全ての項目を入力してください', 400

        password = generate_password_hash(password_raw, method='pbkdf2:sha256')
        birthdate = f"{birth_year}-{birth_month}-{birth_day}"

        existing_user = User.query.filter_by(email=email).first()
        existing_name = User.query.filter_by(username=username).first()

        if existing_user and existing_user.is_verified:
            return 'このメールアドレスは既に使われています'
        if existing_name and (not existing_user or existing_user.username != username):
            return 'このユーザー名は既に使われています'

        if existing_user and not existing_user.is_verified:
            existing_user.username = username
            existing_user.password = password
            existing_user.birthdate = birthdate
            existing_user.gender = gender
            existing_user.occupation = occupation
            existing_user.prefecture = prefecture
            db.session.commit()
            send_confirmation_email(email, username)
            return '未確認アカウントを更新しました。メールをご確認ください。'

        new_user = User(
            username=username,
            email=email,
            password=password,
            birthdate=birthdate,
            gender=gender,
            occupation=occupation,
            prefecture=prefecture
        )
        db.session.add(new_user)
        db.session.commit()
        send_confirmation_email(email, username)
        return '確認メールを送信しました。メール内のリンクをクリックして登録を完了してください。'

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        identifier = request.form['username']
        password = request.form['password']
        user = User.query.filter((User.username == identifier) | (User.email == identifier)).first()
        if not user or not check_password_hash(user.password, password):
            return 'ログイン失敗（ユーザー名・メールアドレスまたはパスワード）'
        if not user.is_verified:
            return 'メールアドレスの確認が完了していません。メール内のリンクをご確認ください。'
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
    csv_path = f"recordings/user_{current_user.id}_scores.csv"
    first_score = None
    latest_score = None
    diff = None
    first_score_date = None

    if os.path.exists(csv_path):
        with open(csv_path, 'r') as csvfile:
            reader = list(csv.reader(csvfile))
            if reader:
                first_score_date = reader[0][0].split(" ")[0]
                first_score = int(reader[0][1])
                latest_score = int(reader[-1][1])
                diff = latest_score - first_score

    return render_template(
        'dashboard.html',
        user=current_user,
        first_score=first_score,
        first_score_date=first_score_date,
        latest_score=latest_score,
        diff=diff
    )

@app.route('/record')
@login_required
def record():
    return render_template('record.html')

@app.route('/upload', methods=['POST'])
@login_required
def upload():
    if 'audio_data' not in request.files:
        return '音声データが見つかりません'
    file = request.files['audio_data']
    if file.filename == '':
        return 'ファイルが選択されていません'

    UPLOAD_FOLDER = 'uploads'
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    now_str = now.strftime('%Y%m%d_%H%M%S')
    filename = f"user{current_user.id}_{now_str}.webm"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    scores_dir = 'recordings'
    os.makedirs(scores_dir, exist_ok=True)

    csv_path = os.path.join(scores_dir, f"user_{current_user.id}_scores.csv")
    if os.path.exists(csv_path):
        with open(csv_path, 'r') as csvfile:
            for row in csv.reader(csvfile):
                if row[0].startswith(today_str):
                    return '本日はすでに保存済みです（1日1回制限）'

    wav_filename = filename.replace(".webm", ".wav")
    wav_path = os.path.join(UPLOAD_FOLDER, wav_filename)
    convert_webm_to_wav(filepath, wav_path)

    if not is_valid_wav(wav_path):
        flash("録音に失敗しました。もう一度お試しください。")
        return redirect(url_for("record"))

    stress_score = analyze_stress_from_wav(wav_path)

    with open(csv_path, 'a', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([now.strftime('%Y-%m-%d %H:%M:%S'), stress_score])

    return 'アップロード成功！'

@app.route('/result')
@login_required
def result():
    dates = []
    scores = []
    csv_path = f"recordings/user_{current_user.id}_scores.csv"
    if os.path.exists(csv_path):
        with open(csv_path, 'r') as csvfile:
            for row in csv.reader(csvfile):
                dates.append(row[0])
                scores.append(int(row[1]))
    return render_template('result.html', dates=dates, scores=scores)

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
