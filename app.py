from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime
import random
import csv

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# ユーザーモデル
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False, unique=True)
    password = db.Column(db.String(200), nullable=False)
    birthdate = db.Column(db.String(20))
    gender = db.Column(db.String(10))
    occupation = db.Column(db.String(100))
    prefecture = db.Column(db.String(20))

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/')
def home():
    return 'これはストレスチェックアプリのトップページです。 <a href="/login">ログイン</a> or <a href="/register">新規登録</a>'

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = generate_password_hash(request.form['password'], method='pbkdf2:sha256')
        birthdate = f"{request.form['birth_year']}-{request.form['birth_month']}-{request.form['birth_day']}"
        gender = request.form['gender']
        occupation = request.form['occupation']
        prefecture = request.form['prefecture']

        if User.query.filter_by(username=username).first():
            return 'このユーザー名は既に使われています'

        new_user = User(
            username=username,
            password=password,
            birthdate=birthdate,
            gender=gender,
            occupation=occupation,
            prefecture=prefecture
        )
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()

        if not user or not check_password_hash(user.password, password):
            return 'ログイン失敗（ユーザー名またはパスワード）'

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

    if os.path.exists(csv_path):
        with open(csv_path, 'r') as csvfile:
            reader = list(csv.reader(csvfile))
            if reader:
                first_score = int(reader[0][1])
                latest_score = int(reader[-1][1])
                diff = latest_score - first_score

    return render_template(
        'dashboard.html',
        user=current_user,
        first_score=first_score,
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
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)

    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    now_str = now.strftime('%Y%m%d_%H%M%S')
    filename = f"user{current_user.id}_{now_str}.webm"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    scores_dir = 'recordings'
    if not os.path.exists(scores_dir):
        os.makedirs(scores_dir)

    csv_path = os.path.join(scores_dir, f"user_{current_user.id}_scores.csv")

    # 1日1回保存制限のチェック
    if os.path.exists(csv_path):
        with open(csv_path, 'r') as csvfile:
            reader = csv.reader(csvfile)
            for row in reader:
                if row[0].startswith(today_str):
                    return '本日はすでに保存済みです（1日1回制限）'

    stress_score = random.randint(50, 90)

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
            reader = csv.reader(csvfile)
            for row in reader:
                dates.append(row[0])
                scores.append(int(row[1]))

    return render_template('result.html', dates=dates, scores=scores)

if __name__ == '__main__':
    with app.app_context():
        if not os.path.exists('users.db'):
            db.create_all()
    app.run(debug=True)