import threading
import time
from collections import OrderedDict

try:
    import RPi.GPIO as GPIO  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - expected on non-Raspberry Pi systems
    GPIO = None


REED_MAP = OrderedDict(
    [
        ("Monday", 24),
        ("Tuesday", 18),
        ("Wednesday", 26),
        ("Thursday", 5),
        ("Friday", 17),
        ("Saturday", 6),
        ("Sunday", 21),
    ]
)

lock = threading.Lock()
reed_status: dict[str, dict[str, object]] = {}
_monitor_started = False


def _read_pin_value(pin: int) -> int:
    if GPIO is None:
        return 1
    return int(GPIO.input(pin))


def _state_for_value(value: int) -> str:
    return "OPEN" if value == 1 else "CLOSED"


def _seed_initial_state() -> None:
    with lock:
        for day, pin in REED_MAP.items():
            value = _read_pin_value(pin)
            state = _state_for_value(value)
            reed_status[day] = {
                "state": state,
                "value": value,
                "progress": 1 if state == "CLOSED" else 0,
                "correct": False,
            }


def initialize_gpio() -> None:
    if GPIO is None:
        _seed_initial_state()
        return

    GPIO.setmode(GPIO.BCM)
    for pin in REED_MAP.values():
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    _seed_initial_state()


def update_reed_states() -> None:
    while True:
        with lock:
            for day, pin in REED_MAP.items():
                current_value = _read_pin_value(pin)
                current_state = _state_for_value(current_value)

                item = reed_status[day]
                previous_state = item["state"]

                item["value"] = current_value
                item["state"] = current_state

                if item["correct"]:
                    continue

                if item["progress"] == 0:
                    if current_state == "CLOSED":
                        item["progress"] = 1
                elif item["progress"] == 1:
                    if previous_state == "CLOSED" and current_state == "OPEN":
                        item["progress"] = 2
                elif item["progress"] == 2:
                    if previous_state == "OPEN" and current_state == "CLOSED":
                        item["progress"] = 3
                        item["correct"] = True

        time.sleep(0.05)


def get_reed_status() -> dict[str, dict[str, object]]:
    with lock:
        return {
            day: {
                "state": data["state"],
                "value": data["value"],
                "progress": data["progress"],
                "correct": data["correct"],
            }
            for day, data in reed_status.items()
        }


def reset_reed_status() -> None:
    with lock:
        for day, pin in REED_MAP.items():
            value = _read_pin_value(pin)
            state = _state_for_value(value)
            reed_status[day]["state"] = state
            reed_status[day]["value"] = value
            reed_status[day]["progress"] = 1 if state == "CLOSED" else 0
            reed_status[day]["correct"] = False


def start_monitoring() -> None:
    global _monitor_started
    if _monitor_started:
        return

    initialize_gpio()
    threading.Thread(target=update_reed_states, daemon=True).start()
    _monitor_started = True


start_monitoring()
