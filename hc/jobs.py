"""定期処理（本体の中のスレッドで動く）

  午前2:00  申込書控えの日次ジョブ（事前通知・削除・残存チェック）。PC が止まっていた場合は、次に起動したときに追いつく
  毎朝8:30  タスクのサマリー（社内通知）
  1時間ごと LINE のトーク・添付（90日）、完了したタスク（1年）、成約しなかった見積依頼書（1年）の削除
  1分ごと   フォルダ監視（data/inbox/）
"""
import datetime as dt, threading, time, traceback
from . import db, vault, line, rag, tasks, audit
from .config import DELETE_HOUR, TASK_RETENTION_DAYS, QUOTE_RETENTION_DAYS


def retention():
    line.purge()
    db.run("DELETE FROM task WHERE status='完了' AND done_at < datetime('now','localtime',?)", f"-{TASK_RETENTION_DAYS} days")
    db.run("DELETE FROM quote WHERE status!='成約' AND created_at < datetime('now','localtime',?)", f"-{QUOTE_RETENTION_DAYS} days")


def tick(now=None):
    now = now or dt.datetime.now()
    today = now.date().isoformat()
    if now.hour >= DELETE_HOUR and db.setting("vault_last_run") != today:
        vault.run_daily(now=now.date())
    if (now.hour, now.minute) >= (8, 30) and db.setting("morning_last") != today:
        tasks.morning_notice()
        db.set_setting("morning_last", today)
    if time.time() - float(db.setting("retention_last", "0")) > 3600:
        retention()
        db.set_setting("retention_last", time.time())
    rag.scan_inbox()


def loop():
    while True:
        try:
            tick()
        except Exception as e:
            traceback.print_exc()
            audit.notify("定期処理でエラーが起きました", str(e)[:300], level="error", key=f"joberr:{dt.date.today()}:{type(e).__name__}")
        time.sleep(60)


def start():
    threading.Thread(target=loop, daemon=True, name="hokenchat-jobs").start()
