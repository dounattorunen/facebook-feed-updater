"""
pinterest_bulk.csv の最後の予約日を調べ、残りが REMIND_DAYS 日以内なら
GitHub Actions の出力 remind=true と、最終予約日（日本時間）を返す。
"""
import csv
import os
from datetime import datetime, timedelta, timezone

REMIND_DAYS = 2
JST = timezone(timedelta(hours=9))

def main():
    last = None
    with open("pinterest_bulk.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                d = datetime.strptime(row["Publish date"], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            except (KeyError, ValueError):
                continue
            last = d if last is None or d > last else last
    today = datetime.now(JST).date()
    last_jst = last.astimezone(JST).date() if last else today
    days_left = (last_jst - today).days
    remind = days_left <= REMIND_DAYS
    print(f"最後の予約日: {last_jst}（残り {days_left} 日） → 通知: {remind}")
    with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a", encoding="utf-8") as out:
        out.write(f"remind={'true' if remind else 'false'}\n")
        out.write(f"last_date={last_jst}\n")
        out.write(f"days_left={days_left}\n")

if __name__ == "__main__":
    main()
