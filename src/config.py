import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


def _int_env(name: str, default: int = 0) -> int:
  value = os.environ.get(name)
  if value is None or value == "":
    return default
  return int(value)


def _float_env(name: str, default: float = 0.0) -> float:
  value = os.environ.get(name)
  if value is None or value == "":
    return default
  return float(value)


def _bool_env(name: str, default: bool = False) -> bool:
  value = os.environ.get(name)
  if value is None:
    return default
  return value.lower() == "true"


@dataclass(frozen=True)
class OpenAIConfig:
  api_key: str | None
  api_url: str | None
  model: str | None
  max_tokens: int
  temperature: float


@dataclass(frozen=True)
class VoiceNotificationConfig:
  enabled: bool
  leave_message: str
  join_message: str
  channel_name: str


@dataclass(frozen=True)
class AppConfig:
  discord_token: str | None
  character_prompt: str
  serp_api_key: str | None
  openai: OpenAIConfig
  voice_notification: VoiceNotificationConfig


def load_config() -> AppConfig:
  return AppConfig(
    discord_token=os.environ.get("DISCORD_BOT_TOKEN"),
    character_prompt=os.environ.get("CHARACTER_PROMPT") or "",
    serp_api_key=os.environ.get("SERP_API_KEY"),
    openai=OpenAIConfig(
      api_key=os.environ.get("OPEN_AI_API_KEY"),
      api_url=os.environ.get("OPEN_AI_API_URL"),
      model=os.environ.get("OPEN_AI_MODEL"),
      max_tokens=_int_env("OPEN_AI_MAX_TOKEN"),
      temperature=_float_env("TEMPERATURE", 1),
    ),
    voice_notification=VoiceNotificationConfig(
      enabled=_bool_env("VOICE_NOTIFICATION_ENABLED"),
      leave_message=os.environ.get("VOICE_LEAVE_MESSAGE", "{name}が{channel}からきえてくにゃ・・・"),
      join_message=os.environ.get("VOICE_JOIN_MESSAGE", "{name}が{channel}に入ったにゃ！"),
      channel_name=os.environ.get("VOICE_NOTIFICATION_CHANNEL", "general"),
    ),
  )
