import random
import time
from datetime import datetime, timedelta


def next_2am():
    now = datetime.now()
    target = now.replace(hour=2, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return target

while True:
    run_at = next_2am()
    sleep_seconds = (run_at - datetime.now()).total_seconds()
    time.sleep(max(0, sleep_seconds))

    value = 1 if random.random() < 0.8 else 0
    if value == 1:
        # Random morning publish time from 6:00 AM through 11:59 AM.
        published_time = run_at.replace(
            hour=random.randint(6, 11),
            minute=random.randint(0, 59),
            second=random.randint(0, 59),
            microsecond=0,
        )
    else:
        published_time = run_at

    print(f"{published_time.isoformat()} -> {value}")
