# app_instance.py
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

app = Flask(__name__)  # 定義だけ
db = SQLAlchemy()
login_manager = LoginManager()
