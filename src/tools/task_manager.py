import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from logging import getLogger
from typing import Any, Awaitable, Callable

import pytz
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = getLogger(__name__)


@dataclass(frozen=True)
class ScheduledTask:
  task_id: str
  channel_id: int
  prompt: str
  run_at: datetime

  def to_dict(self) -> dict[str, Any]:
    return {
      "task_id": self.task_id,
      "channel_id": self.channel_id,
      "prompt": self.prompt,
      "run_at": self.run_at.isoformat(timespec="seconds"),
    }


class TaskManager:
  """In-process scheduler for one-shot agent tasks. Tasks do not survive a restart."""

  def __init__(self, timezone='Asia/Tokyo'):
    self.timezone = pytz.timezone(timezone)
    self.scheduler = AsyncIOScheduler(event_loop=asyncio.get_event_loop())
    self._tasks: dict[str, ScheduledTask] = {}

  def start_scheduler(self):
    """スケジューラを起動"""
    self.scheduler.start()
    logger.info("Scheduler has started!")

  def add_task(
    self,
    func: Callable[[int, str], Awaitable[None]],
    run_date: datetime,
    channel_id: int,
    prompt: str,
  ) -> ScheduledTask:
    """タスクを追加"""
    if run_date.tzinfo is None:
      run_date = self.timezone.localize(run_date)
    task = ScheduledTask(
      task_id=uuid.uuid4().hex[:8],
      channel_id=channel_id,
      prompt=prompt,
      run_at=run_date,
    )

    async def run():
      self._tasks.pop(task.task_id, None)
      await func(channel_id, prompt)

    self._tasks[task.task_id] = task
    try:
      self.scheduler.add_job(run, 'date', run_date=run_date, id=task.task_id, misfire_grace_time=300)
    except Exception:
      self._tasks.pop(task.task_id, None)
      raise
    logger.info(f"Task has been added: {task.task_id}")
    return task

  def list_tasks(self, channel_id: int | None = None) -> list[ScheduledTask]:
    tasks = [
      task for task in self._tasks.values()
      if channel_id is None or task.channel_id == channel_id
    ]
    return sorted(tasks, key=lambda task: task.run_at)

  def cancel_task(self, task_id: str, channel_id: int | None = None) -> bool:
    task = self._tasks.get(task_id)
    if task is None or (channel_id is not None and task.channel_id != channel_id):
      return False
    self._tasks.pop(task_id, None)
    try:
      self.scheduler.remove_job(task_id)
    except JobLookupError:
      return False
    logger.info(f"Task has been cancelled: {task_id}")
    return True
