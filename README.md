# meowgent
Pythonで構築されたカスタマイズ可能なインタラクティブDiscordボットです。LLMのapiを利用した自然言語処理、ボイスチャンネル通知、スタミナシステムなどがあります。


## Features
- インタラクティブチャット: ユーザーのメッセージに応答し、個性や挙動を自由に設定可能 (CHARACTER_PROMPT)
- ボイスチャンネル通知: ユーザーの入退室をテキストチャンネルでお知らせ。通知内容は自由にカスタマイズ可能 (VOICE_NOTIFICATION_ENABLED)
- スタミナシステム: ボットの返信確率や頻度をスタミナとして管理。スタミナは時間経過で回復します。
- ツールの統合: Web検索などの外部ツールをサポート (SERP API)
- 環境変数による設定: ボットの挙動やメッセージを環境変数で簡単に設定可能。


## Setup
- Python 3.10+
- Discord Bot Token
- OpenAI API Key
- Required Python libraries (see src/requirements.txt)

### Installation
```sh
git clone https://github.com/yourusername/meowgent.git
cd meowgent
```

```sh
python3 -m venv venv
source venv/bin/activate
pip install -r src/requirements.txt
```

```sh
python src/bot.py
```

## とりあえずChatGPTを使ったおれおれDiscord botが欲しい人
- https://discord.com/developers/applications Discord Tokenを取得する
- git cloneする
- fly.ioのアカウントを作ったりする
- flyctlをインストールしたりする (https://fly.io/docs/hands-on/install-flyctl/)
- プロンプトとか環境変数を.env.exampleを参考に設定する
```
fly deploy
```

## update requirements.txt
```
venv/bin/pipreqs --force src
```


### yukariちゃん
実際に運用されているbotの例です。
雑談してくれたり、誰かがボイスチャンネルに参加したことや、Cryptoの価格を教えてくれるDiscord botです

<img src="https://github.com/xsota/discord_bot/assets/5690642/bcf8670f-240a-4f2c-8ba2-aba5c02a6323" alt="かわいい顔の画像" width=320>

![image](https://github.com/xsota/discord_bot/assets/5690642/c43b10d7-5492-434a-83e9-710cb129f8ad)

![image](https://github.com/xsota/discord_bot/assets/5690642/4543f503-045a-46c8-9d69-1d5bf3bc557a)


## Contributing
Contributions are welcome! Feel free to submit issues or pull requests. Please ensure all new features are well-documented and tested.

For detailed guidelines on how to contribute, please refer to the [CONTRIBUTING.md](./CONTRIBUTING.md) file.

