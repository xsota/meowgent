# meowgent
Pythonで構築されたカスタマイズ可能なインタラクティブDiscordボットです。LLMのapiを利用した自然言語処理、ボイスチャンネル通知、スタミナシステムなどがあります。


## Features
- インタラクティブチャット: ユーザーのメッセージに応答し、個性や挙動を自由に設定可能 (CHARACTER_PROMPT)
- ボイスチャンネル通知: ユーザーの入退室をテキストチャンネルでお知らせ。通知内容は自由にカスタマイズ可能 (VOICE_NOTIFICATION_ENABLED)
- スタミナシステム: ボットの返信確率や頻度をスタミナとして管理。スタミナは時間経過で回復します。
- ツールの統合: Web検索などの外部ツールをサポート (SERP API)
- 環境変数による設定: ボットの挙動やメッセージを環境変数で簡単に設定可能。

## Agent runtime

meowgentは1回の返信の中でToolを複数ステップ・並列に使って調べてから答えます。

| Tool | 内容 |
| --- | --- |
| `web_search` | Web検索 (上位5件のタイトル・URL・スニペット) |
| `fetch_url` | 公開Webページの本文を取得。プライベートIP・非標準ポート宛ては拒否 |
| `read_channel_history` | 今いるチャンネルの過去メッセージを最大50件読む |
| `create_task` / `list_tasks` / `cancel_task` | 今いるチャンネルでの予約実行 (リマインダーや後追い確認)。再起動で消えます |
| `get_current_time` | 現在時刻 |
| `remember_memory` / `search_memory` | 長期記憶 (Memory API設定時のみ) |

- `AGENT_MAX_STEPS` (default 8): 1返信で使えるToolラウンド数。使い切るとToolなしで最終回答を生成します。
- `AGENT_TOOL_TIMEOUT` (default 30): Tool 1回あたりのタイムアウト秒。
- 2000文字を超える返信は複数メッセージに分割して送信します。長い回答をさせたい場合は `OPEN_AI_MAX_TOKEN` も引き上げてください。

## Long-term memory (Phase 1)

Cloudflare Worker + D1で構成する長期記憶APIは [`memory-worker/`](./memory-worker/) にあります。Python Discord BotからD1へ直接接続せず、Memory APIを経由する構成です。PR1のAPI基盤に加えて、PR2では会話中にmeowgent自身が使う`remember_memory` / `search_memory` Toolを実装しています。短期会話履歴は従来どおりPython側で保持します。

Active Memoryを有効にするには、`MEMORY_API_URL`と`MEMORY_API_TOKEN`を設定してください。Token未設定時はMemory Toolを無効化します。

セットアップとデプロイ手順は [`memory-worker/README.md`](./memory-worker/README.md) を参照してください。


## Setup
- Python 3.12+
- Discord Bot Token
- OpenAI API Key
- Required Python libraries (see src/requirements.txt)

### Installation
```sh
git clone https://github.com/xsota/meowgent.git

cd meowgent
uv sync
cp .env.example .env # 必要な値を .env に記入
uv run python src/bot.py
```

## 自分専用のDiscord AI botを作る
- https://discord.com/developers/applications Discord Tokenを取得する
- git cloneする
- fly.ioのアカウントを作ったりする
- flyctlをインストールしたりする (https://fly.io/docs/hands-on/install-flyctl/)
- プロンプトとか環境変数を.env.exampleを参考に設定する
```
fly deploy
```


### yukariちゃん
実際に運用されているbotの例です。
雑談してくれたり、誰かがボイスチャンネルに参加したことを教えてくれるDiscord botです

<img width="256" height="256" alt="image" src="https://github.com/user-attachments/assets/bc7d7989-9b7b-46b5-b51a-a4bb5cd8a287" alt="yukariの画像" width=320 />

<img width="384" height="155" alt="image" src="https://github.com/user-attachments/assets/3ae7e4e2-f833-472f-af45-a4fbf01bcdaf" />




## Contributing
Contributions are welcome! Feel free to submit issues or pull requests. Please ensure all new features are well-documented and tested.

For detailed guidelines on how to contribute, please refer to the [CONTRIBUTING.md](./CONTRIBUTING.md) file.
