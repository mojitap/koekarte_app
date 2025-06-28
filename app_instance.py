from flask import Flask
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config.from_object('config.Config')  # DB URIなどを読み込む
db = SQLAlchemy(app)  # ← 直接バインド方式に変更（推奨）
