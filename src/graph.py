import os
from dotenv import load_dotenv

load_dotenv()

from llm import OpenAICompatibleChatProvider, ToolDefinition
from meowgent import Meowgent

character_prompt = os.environ.get('CHARACTER_PROMPT')


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
  model=os.environ.get('OPEN_AI_MODEL'),
  api_key=os.environ.get('OPEN_AI_API_KEY'),
  base_url=os.environ.get('OPEN_AI_API_URL'),
  max_tokens=int(os.environ.get('OPEN_AI_MAX_TOKEN')),
  temperature=float(os.environ.get('TEMPERATURE', 1))
)
print("[INFO] モデルが初期化されました。")

meowgent = Meowgent(
  provider=provider,
  tools=tools,
  system_prompt=character_prompt,
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
