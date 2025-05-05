# koekarte\_app

"コエカルテ" は、音声によるストレスチェックを行う Flask 製の Web アプリケーションです。

## 🧠 主な機能

* 音声を録音してストレススコアを測定（pyAudioAnalysis 使用）
* スコアを日々記録してグラフ表示（Chart.js）
* ログイン／新規登録機能（メール認証付き）
* 管理者ページで全ユーザーの記録を確認
* スマートフォンでも見やすいレスポンシブデザイン

## 🛠 使用技術

* Flask / Flask-Login / Flask-Mail
* SQLAlchemy（PostgreSQL）
* Chart.js
* pyAudioAnalysis
* Gunicorn + Render.com によるデプロイ

## 🔧 セットアップ手順（ローカル環境）

1. リポジトリをクローン

```
git clone https://github.com/yourname/koekarte_app.git
cd koekarte_app
```

2. `.env` ファイルを作成

`.env.example` をコピーして `.env` を作成し、各項目を自分の設定に変更してください。

```
cp .env.example .env
```

3. 必要なライブラリをインストール

```
pip install -r requirements.txt
```

4. アプリを実行

```
python app.py
```

5. ブラウザで `http://localhost:5000` にアクセス

## 📁 フォルダ構成（一部）

```
.
├── app.py
├── templates/
│   ├── base.html
│   ├── register.html
│   ├── login.html
│   ├── dashboard.html
│   ├── record.html
│   ├── result.html
│   └── admin.html
├── static/
│   └── recorder.js
├── uploads/
│   └── .keep
├── .env.example
├── requirements.txt
└── README.md
```

## 🔐 今後の予定機能

* 🔄 プロフィール編集（地域・性別・生年月日・職業）
* 🔁 パスワード再発行（確認メール送信）
* 📊 スコア比較：過去との比較メッセージ追加
* 📝 利用規約・プライバシーポリシー表示（すでに実装済み）

## 👤 管理者アクセス

管理者メールアドレスを `.env` に以下のように追加してください：

```
ADMIN_EMAIL=youradmin@email.com
```

## 🔒 セキュリティ上の注意

* `.env` ファイルは **絶対に GitHub にアップロードしないでください**
* `uploads/*.webm` など個人の音声ファイルも `.gitignore` により除外しています

---

## 🎉 Special Thanks

* Flask
* pyAudioAnalysis
* Chart.js
* Render.com

---

開発者: \[yourname]（メールアドレス省略可）
