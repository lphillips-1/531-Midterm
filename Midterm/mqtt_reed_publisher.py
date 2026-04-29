import json
import os
import threading
import time
from datetime import datetime

import paho.mqtt.client as mqtt
import RPi.GPIO as GPIO

GPIO.setmode(GPIO.BCM)

GPIO_TO_DAY = {
    21: "Sunday",
    24: "Monday",
    18: "Tuesday",
    26: "Wednesday",
    5: "Thursday",
    17: "Friday",
    6: "Saturday",
}

MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
MQTT_TOPIC_ROOT = os.getenv("MQTT_TOPIC_ROOT", "medconnect")
MQTT_COMMAND_TOPIC = f"{MQTT_TOPIC_ROOT}/commands/new_week"

lock = threading.Lock()
sensor_data = {}


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


for pin in GPIO_TO_DAY:
    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)


for pin, day in GPIO_TO_DAY.items():
    value = GPIO.input(pin)
    state = "OPEN" if value == GPIO.HIGH else "CLOSED"
    sensor_data[day] = {
        "pin": pin,
        "value": int(value),
        "state": state,
        "progress": 0,
        "correct": False,
    }


def topic_for(day):
    return f"{MQTT_TOPIC_ROOT}/sensors/{day.lower()}"


def payload_for(day):
    item = sensor_data[day]
    return {
        "day": day,
        "pin": item["pin"],
        "value": item["value"],
        "state": item["state"],
        "progress": item["progress"],
        "correct": item["correct"],
        "timestamp": now_str(),
    }


def publish_day(client, day):
    client.publish(
        topic_for(day),
        json.dumps(payload_for(day)),
        qos=1,
        retain=True,
    )


def publish_all(client):
    for day in GPIO_TO_DAY.values():
        publish_day(client, day)


def reset_progress(client):
    with lock:
        for pin, day in GPIO_TO_DAY.items():
            value = GPIO.input(pin)
            state = "OPEN" if value == GPIO.HIGH else "CLOSED"
            sensor_data[day]["value"] = int(value)
            sensor_data[day]["state"] = state
            sensor_data[day]["progress"] = 0
            sensor_data[day]["correct"] = False
            publish_day(client, day)


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(MQTT_COMMAND_TOPIC)
        publish_all(client)


def on_message(client, userdata, message):
    if message.topic == MQTT_COMMAND_TOPIC:
        reset_progress(client)


def update_sensor_states(client):
    while True:
        with lock:
            for pin, day in GPIO_TO_DAY.items():
                value = GPIO.input(pin)
                state = "OPEN" if value == GPIO.HIGH else "CLOSED"
                item = sensor_data[day]
                previous_state = item["state"]
                previous_progress = item["progress"]
                previous_correct = item["correct"]

                item["value"] = int(value)
                item["state"] = state

                if not item["correct"]:
                    if item["progress"] == 0 and state == "CLOSED":
                        item["progress"] = 1
                    elif item["progress"] == 1 and previous_state == "CLOSED" and state == "OPEN":
                        item["progress"] = 2
                    elif item["progress"] == 2 and previous_state == "OPEN" and state == "CLOSED":
                        item["progress"] = 3
                        item["correct"] = True

                if (
                    previous_state != item["state"]
                    or previous_progress != item["progress"]
                    or previous_correct != item["correct"]
                ):
                    publish_day(client, day)

        time.sleep(0.05)


def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_start()

    try:
        update_sensor_states(client)
    finally:
        client.loop_stop()
        client.disconnect()
        GPIO.cleanup()


if __name__ == "__main__":
    main()
