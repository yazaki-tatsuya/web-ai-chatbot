# Web AI Chatbot

OpenAI Realtime APIを利用した、ブラウザ音声対話型のコミュニケーション練習アプリです。
シナリオ選択、音声対話、会話ログ保存、AIフィードバック生成、履歴確認に対応しています。

## 必要な環境

- Python 3.11
- Node.js 24（JavaScriptテストを実行する場合）
- OpenAI APIキー
- マイクを使用できるWebブラウザ

Azure PipelinesでもPython 3.11とNode.js 24を使用しています。

## セットアップ

### 1. Python仮想環境を作成する

PowerShellの例：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### 2. 環境変数を設定する

`.env.example`を`.env`としてコピーし、各値を設定します。

```powershell
Copy-Item .env.example .env
```

最低限、次の値を変更してください。

```dotenv
FLASK_SECRET_KEY=十分に長いランダムな値
OPENAI_API_KEY=OpenAIのAPIキー
```

`FLASK_SECRET_KEY`の生成例：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

> [!IMPORTANT]
> `.env`やAPIキーをGitへコミットしないでください。`.env`は`.gitignore`の対象です。

## 起動

```powershell
python test_OpenAI_WebUI.py
```

起動後、ブラウザで <http://localhost:5000/home> を開きます。

音声対話にはブラウザのマイク使用許可が必要です。通常のブラウザでは、`localhost`以外からマイクを利用する場合にHTTPSが必要です。

## 環境変数

| 変数 | 必須 | 既定値 | 用途 |
|---|---:|---|---|
| `FLASK_SECRET_KEY` | 本番では必須 | 開発用の非安全な値 | Flaskセッションの署名 |
| `OPENAI_API_KEY` | OpenAI機能利用時 | なし | Realtime API・フィードバック生成 |
| `OPENAI_REALTIME_MODEL` | 任意 | `gpt-realtime-2.1` | Realtimeモデル |
| `OPENAI_REALTIME_VOICE` | 任意 | `marin` | Realtime音声 |
| `FEEDBACK_MODEL` | 任意 | `gpt-4o-mini` | フィードバック生成モデル |
| `APP_PIN` | 任意 | なし（認証無効） | 簡易PIN認証 |
| `SQLITE_PATH` | 任意 | `app.db` | SQLiteデータベースの保存先 |
| `SCENARIOS_PATH` | 任意 | `scenarios.json` | シナリオ定義ファイル |
| `ENABLE_LEGACY_OPENAI_WS` | 任意 | `0` | 旧Socket.IO／WebSocket経路を有効化 |

旧環境との互換用として`OPEN_AI_KEY`も認識しますが、新規設定では`OPENAI_API_KEY`を使用してください。

## テスト

### Python構文チェック

```powershell
python -m py_compile test_OpenAI_WebUI.py session_store.py test_realtime_api.py
```

### Pythonユニットテスト

PowerShellの例：

```powershell
$env:FLASK_SECRET_KEY = 'test-only-not-for-production'
$env:SQLITE_PATH = ':memory:'
python -m unittest -v test_realtime_api.py
```

### JavaScriptユニットテスト

```powershell
node --test tests/*.test.cjs
```

CIでは、Python構文チェック、Pythonユニットテスト、JavaScriptユニットテストがすべて成功した場合のみ、後続のデプロイ処理へ進みます。

## 主なファイル

| パス | 内容 |
|---|---|
| `test_OpenAI_WebUI.py` | Flask／Socket.IOアプリケーション本体 |
| `session_store.py` | シナリオ・セッション・会話ログ・フィードバックのSQLite保存 |
| `scenarios.json` | 練習シナリオ定義 |
| `templates/` | 各画面のJinjaテンプレートとブラウザ側処理 |
| `tests/` | Node.js標準テストランナーを使ったブラウザ処理のテスト |
| `test_realtime_api.py` | Python標準`unittest`によるAPI・画面テスト |
| `azure-pipelines.yml` | テスト・ZIP作成・Azure App Serviceデプロイ |

## データと秘密情報

- 既定ではセッション、会話ログ、フィードバックを`app.db`へ保存します。
- `*.db`、`.env`、仮想環境、PythonキャッシュはGit管理対象外です。
- APIキーやPINをコード、README、テストデータへ直接記載しないでください。
- 本番環境では、十分に長い`FLASK_SECRET_KEY`を環境ごとに設定してください。
