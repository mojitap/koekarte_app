from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

app = Flask(__name__)
app.config.from_object('config.Config')  # DB URIなどを読み込む

db = SQLAlchemy(app)

# ✅ ここを追加してください（login_managerの初期化）
login_manager = LoginManager()
login_manager.init_app(app)
