from config import load_config
from llm import OpenAICompatibleChatProvider, ToolDefinition
from meowgent import Meowgent

config = load_config()


def search(query: str):
  """Web検索を行うツール"""
  print(f"[search] ツールが呼び出されました。クエリ: {query}")
  return "最高のそばはアルティメットそーばと言われていますが"


tools = [
  ToolDefinition(
    name="search",
    description="Web検索を行うツール",
    parameters={
      "type": "object",
      "properties": {
        "query": {
          "type": "string",
          "description": "Search query.",
        },
      },
      "required": ["query"],
    },
    handler=search,
  )
]

provider = OpenAICompatibleChatProvider(
  model=config.openai.model,
  api_key=config.openai.api_key,
  base_url=config.openai.api_url,
  max_tokens=config.openai.max_tokens,
  temperature=config.openai.temperature
)
print("[INFO] モデルが初期化されました。")

meowgent = Meowgent(
  provider=provider,
  tools=tools,
  system_prompt=config.character_prompt,
)

print("[INFO] 実行を開始します。")
final_state = meowgent.app.invoke(
  {
    "messages": [{"role": "user", "content": "最高のそばを教えて"}],
    "current_channel_id": 42,
  },
  config={"configurable": {"thread_id": 42, "recursion_limit": 5}}
)
print("[INFO] 実行が完了しました。")

print(f"[RESULT] 最後の応答内容: {final_state['messages'][-1].content}")
