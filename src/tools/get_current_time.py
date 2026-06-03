from datetime import datetime
import pytz

def get_current_time(timezone_name="Asia/Tokyo"):
  """
  Get current time in the specified timezone.
  Args: timezone_name (str): Timezone name (default: "Asia/Tokyo").
  """
  try:
    # 指定されたタイムゾーンを取得
    timezone = pytz.timezone(timezone_name)
  except pytz.UnknownTimeZoneError:
    return {"error": f"Unknown timezone: {timezone_name}"}

  # 現在時刻を指定されたタイムゾーンで取得
  current_time = datetime.now(timezone)
  return current_time.strftime("%Y-%m-%d %H:%M:%S %Z%z")


if __name__ == '__main__':
  print(get_current_time('Etc/UTC'))
  print(get_current_time())
  print(get_current_time('Asia/Dubai'))
  print(get_current_time('America/New_York'))
