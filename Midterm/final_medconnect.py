from flask import Flask, jsonify, render_template_string
import json
import os
import threading
from datetime import datetime
from urllib import error, request

import paho.mqtt.client as mqtt

app = Flask(__name__)

# UI display order
DISPLAY_DAYS = [
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
]

WEEKS_FILE = "weeks_log.json"
MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
MQTT_TOPIC_ROOT = os.getenv("MQTT_TOPIC_ROOT", "medconnect")
MQTT_SENSOR_TOPIC = f"{MQTT_TOPIC_ROOT}/sensors/+"
MQTT_COMMAND_TOPIC = f"{MQTT_TOPIC_ROOT}/commands/new_week"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE_DIR, ".env")
AFFIRMATION_CACHE_FILE = os.path.join(BASE_DIR, "daily_affirmation.json")

lock = threading.Lock()
sensor_data = {}
mqtt_status = {
    "connected": False,
    "last_message_at": None,
}
mqtt_client = None
affirmation_lock = threading.Lock()
affirmation_cache = {}


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_env_file(path):
    if not os.path.exists(path):
        return

    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'").strip('"')
                os.environ.setdefault(key, value)
    except OSError:
        pass


load_env_file(ENV_FILE)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"

FALLBACK_AFFIRMATIONS = [
    "You are safe, supported, and doing a good job. One step at a time is enough.",
    "Today can be gentle. Small steps still count, and you do not have to rush.",
    "You are cared for, and your effort matters. This moment is enough.",
]


def default_week_record():
    return {
        "created_at": now_str(),
        "days": {day: False for day in DISPLAY_DAYS}
    }


def load_weeks():
    if not os.path.exists(WEEKS_FILE):
        return []
    try:
        with open(WEEKS_FILE, "r") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_weeks(data):
    temp_file = WEEKS_FILE + ".tmp"
    with open(temp_file, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(temp_file, WEEKS_FILE)


def ensure_current_week():
    weeks = load_weeks()
    if not weeks:
        weeks.append(default_week_record())
        save_weeks(weeks)
    return weeks


def get_current_week():
    weeks = ensure_current_week()
    return weeks[-1]


def mark_day_complete(day_name):
    weeks = ensure_current_week()
    weeks[-1]["days"][day_name] = True
    save_weeks(weeks)


def create_new_week():
    weeks = ensure_current_week()
    weeks.append(default_week_record())
    save_weeks(weeks)


def load_affirmation_cache():
    if not os.path.exists(AFFIRMATION_CACHE_FILE):
        return {}

    try:
        with open(AFFIRMATION_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_affirmation_cache(data):
    temp_file = AFFIRMATION_CACHE_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(temp_file, AFFIRMATION_CACHE_FILE)


def fallback_affirmation():
    day_index = datetime.now().toordinal() % len(FALLBACK_AFFIRMATIONS)
    return FALLBACK_AFFIRMATIONS[day_index]


def request_daily_affirmation(today_iso):
    if not OPENAI_API_KEY:
        return fallback_affirmation()

    payload = {
        "model": OPENAI_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You write brief daily affirmations for an older adult medication dashboard. "
                    "Keep them calm, warm, and simple. Return only the affirmation text. "
                    "Do not mention medicine, illness, ChatGPT, or AI."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Write one unique affirmation for {today_iso}. "
                    "Limit it to 1 or 2 short sentences."
                ),
            },
        ],
        "temperature": 0.9,
        "max_tokens": 80,
    }

    req = request.Request(
        OPENAI_CHAT_COMPLETIONS_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with request.urlopen(req, timeout=20) as response:
        body = response.read().decode("utf-8")
        data = json.loads(body)

    choices = data.get("choices", [])
    if not choices:
        raise ValueError("No choices returned from OpenAI-compatible API")

    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, list):
        text_parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        content = " ".join(text_parts)

    affirmation = str(content).strip()
    if not affirmation:
        raise ValueError("Empty affirmation returned from OpenAI-compatible API")

    return affirmation


def get_daily_affirmation():
    today_iso = datetime.now().strftime("%Y-%m-%d")

    with affirmation_lock:
        cached = affirmation_cache.get("date")
        text = affirmation_cache.get("text")
        if cached == today_iso and text:
            return text

        disk_cache = load_affirmation_cache()
        if disk_cache.get("date") == today_iso and disk_cache.get("text"):
            affirmation_cache.update(disk_cache)
            return disk_cache["text"]

        try:
            text = request_daily_affirmation(today_iso)
        except (OSError, ValueError, json.JSONDecodeError, error.URLError):
            text = fallback_affirmation()

        affirmation_cache.clear()
        affirmation_cache.update({
            "date": today_iso,
            "text": text,
            "generated_at": now_str(),
        })
        save_affirmation_cache(affirmation_cache)
        return text


def fake_buddies_data():
    return [
        {
            "name": "Doreen",
            "today_percent": 100,
            "month_percent": 82,
            "grid": [
                0,0,0,1,1,1,0,
                0,1,1,1,1,1,0,
                0,1,1,1,1,0,0,
                0,1,1,1,1,1,1
            ]
        },
        {
            "name": "Lauren",
            "today_percent": 0,
            "month_percent": 36,
            "grid": [
                0,0,0,0,0,0,0,
                0,1,0,0,1,0,0,
                0,0,0,1,0,0,0,
                0,1,0,0,0,1,0
            ]
        },
        {
            "name": "Tony",
            "today_percent": 100,
            "month_percent": 91,
            "grid": [
                1,1,1,1,1,0,1,
                1,1,1,1,1,1,1,
                1,1,0,1,1,1,1,
                1,1,1,1,1,1,1
            ]
        }
    ]


def medication_data():
    return [
        {
            "name": "Furosemide 20 mg",
            "frequency": "Confirm schedule",
            "kind": "unspecified"
        },
        {
            "name": "Vitamin B12 1,000 mcg",
            "frequency": "Confirm schedule",
            "kind": "unspecified"
        },
        {
            "name": "Vitamin D3 50 mcg",
            "frequency": "Confirm schedule",
            "kind": "unspecified"
        },
        {
            "name": "Tamsulosin HCl 0.4 mg (Flomax)",
            "frequency": "Confirm schedule",
            "kind": "unspecified"
        },
        {
            "name": "Cranberry fruit extract 500 mg",
            "frequency": "Confirm schedule",
            "kind": "unspecified"
        },
        {
            "name": "Certavite Senior multivitamin",
            "frequency": "Confirm schedule",
            "kind": "unspecified"
        },
        {
            "name": "Docusate-senna (Colace 2 in 1) 50 mg / 8.6 mg tablet",
            "frequency": "As needed",
            "kind": "prn"
        },
        {
            "name": "Alendronate sodium 35 mg (Fosamax)",
            "frequency": "Once weekly",
            "kind": "weekly"
        },
        {
            "name": "Atorvastatin calcium 10 mg",
            "frequency": "Confirm schedule",
            "kind": "unspecified"
        },
        {
            "name": "Eligard 6-month injection",
            "frequency": "Every 6 months · last shot 12/4/2025 · next expected 6/4/2026",
            "kind": "injection"
        },
    ]


# Progress states:
# 0 = waiting for first CLOSED
# 1 = saw first CLOSED, waiting for OPEN
# 2 = saw CLOSED->OPEN, waiting for final CLOSED
# 3 = validated
for day in DISPLAY_DAYS:
    sensor_data[day] = {
        "value": None,
        "state": "UNKNOWN",
        "progress": 0,
        "correct": False,
        "updated_at": None,
    }

current_week = get_current_week()
for day in DISPLAY_DAYS:
    if current_week["days"].get(day, False):
        sensor_data[day]["correct"] = True
        sensor_data[day]["progress"] = 3


def reset_in_memory_for_new_week():
    for day in DISPLAY_DAYS:
        sensor_data[day]["value"] = None
        sensor_data[day]["state"] = "UNKNOWN"
        sensor_data[day]["progress"] = 0
        sensor_data[day]["correct"] = False
        sensor_data[day]["updated_at"] = None


def apply_sensor_message(day, payload):
    if day not in sensor_data:
        return

    with lock:
        item = sensor_data[day]
        previous_state = item["state"]

        if "value" in payload:
            item["value"] = payload["value"]

        state = str(payload.get("state", item["state"])).upper()
        item["state"] = state
        item["updated_at"] = payload.get("timestamp", now_str())

        if item["correct"]:
            return

        if "progress" in payload:
            item["progress"] = int(payload["progress"])

        if payload.get("correct") is True:
            item["progress"] = 3
            item["correct"] = True
            mark_day_complete(day)
            return

        if state not in {"OPEN", "CLOSED"}:
            return

        if item["progress"] == 0:
            if state == "CLOSED":
                item["progress"] = 1

        elif item["progress"] == 1:
            if previous_state == "CLOSED" and state == "OPEN":
                item["progress"] = 2

        elif item["progress"] == 2:
            if previous_state == "OPEN" and state == "CLOSED":
                item["progress"] = 3
                item["correct"] = True
                mark_day_complete(day)


def on_connect(client, userdata, flags, reason_code, properties=None):
    mqtt_status["connected"] = (reason_code == 0)
    if reason_code == 0:
        client.subscribe(MQTT_SENSOR_TOPIC)


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
    mqtt_status["connected"] = False


def on_message(client, userdata, message):
    try:
        payload = json.loads(message.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return

    day = payload.get("day")
    if not day:
        day = message.topic.rsplit("/", 1)[-1]

    if not isinstance(day, str):
        return

    day = day.strip().capitalize()
    mqtt_status["last_message_at"] = now_str()
    apply_sensor_message(day, payload)


def start_mqtt():
    global mqtt_client

    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if MQTT_USERNAME:
        mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message = on_message
    mqtt_client.connect_async(MQTT_BROKER, MQTT_PORT, keepalive=60)
    mqtt_client.loop_start()


def publish_new_week_command():
    if mqtt_client is None or not mqtt_status["connected"]:
        return

    mqtt_client.publish(
        MQTT_COMMAND_TOPIC,
        json.dumps({"timestamp": now_str()}),
        qos=1,
        retain=False,
    )


HTML = """
<!doctype html>
<html>
<head>
    <meta charset="utf-8">
    <title>Allen's Progress Indicator</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            background: #f5f5f5;
            color: #111;
        }
        .page {
            display: grid;
            grid-template-columns: 320px 1fr;
            min-height: 100vh;
            gap: 24px;
            padding: 24px;
            box-sizing: border-box;
        }
        .sidebar, .main-card {
            background: white;
            border-radius: 22px;
            box-shadow: 0 2px 12px rgba(0,0,0,0.06);
        }
        .sidebar {
            padding: 24px;
        }
        .profile-image {
            width: 100%;
            max-width: 220px;
            aspect-ratio: 1 / 1;
            object-fit: cover;
            border-radius: 18px;
            display: block;
            margin: 0 auto 18px auto;
        }
        .welcome {
            font-size: 1.5rem;
            font-weight: bold;
            margin-bottom: 10px;
            text-transform: lowercase;
        }
        .affirmation {
            font-size: 1rem;
            line-height: 1.5;
            color: #4b5563;
            background: #f6f8fa;
            border-radius: 16px;
            padding: 16px;
        }
        .main-card {
            padding: 24px;
        }
        .title {
            font-size: 2rem;
            font-weight: bold;
            margin-bottom: 18px;
        }
        .tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 24px;
            flex-wrap: wrap;
        }
        .tab {
            border: 0;
            background: #eceff3;
            color: #111;
            padding: 10px 16px;
            border-radius: 999px;
            cursor: pointer;
            font-weight: bold;
        }
        .tab.active {
            background: #111;
            color: white;
        }
        .tab-content {
            display: none;
        }
        .tab-content.active {
            display: block;
        }
        .section-title {
            font-size: 1.2rem;
            font-weight: bold;
            margin-bottom: 14px;
        }
        .week-block {
            margin-bottom: 18px;
        }
        .week-label {
            font-weight: bold;
            color: #444;
            margin-bottom: 10px;
        }
        .week-row {
            display: grid;
            grid-template-columns: repeat(7, minmax(74px, 1fr));
            gap: 10px;
        }
        .day-box {
            aspect-ratio: 1 / 1;
            border-radius: 14px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            border: 1px solid #e5e5e5;
            text-align: center;
            padding: 8px;
            box-sizing: border-box;
        }
        .day-box.gray {
            background: #e7ebf0;
            color: #444;
        }
        .day-box.green {
            background: #7bd88a;
            color: #0f3d1b;
        }
        .day-name {
            font-size: 0.82rem;
            font-weight: bold;
            margin-bottom: 6px;
        }
        .day-status {
            font-size: 0.82rem;
            line-height: 1.2;
        }
        .action-row {
            margin-top: 18px;
        }
        .main-button {
            border: 0;
            background: #111;
            color: white;
            padding: 10px 14px;
            border-radius: 10px;
            cursor: pointer;
            font-weight: bold;
        }
        .timestamp {
            margin-top: 12px;
            color: #666;
            font-size: 0.95rem;
        }

        .buddies-list {
            display: grid;
            gap: 14px;
        }
        .buddy-card {
            background: #fafafa;
            border: 1px solid #ececec;
            border-radius: 16px;
            padding: 16px;
            display: grid;
            grid-template-columns: 1fr auto;
            gap: 16px;
            align-items: center;
        }
        .buddy-name {
            font-size: 1.05rem;
            font-weight: bold;
            margin-bottom: 8px;
        }
        .buddy-metrics {
            color: #333;
            line-height: 1.5;
        }
        .mini-grid {
            display: grid;
            grid-template-columns: repeat(7, 10px);
            gap: 4px;
            align-content: start;
        }
        .mini-cell {
            width: 10px;
            height: 10px;
            border-radius: 2px;
            background: #dfe5eb;
        }
        .mini-cell.on {
            background: #7bd88a;
        }

        .med-list {
            display: grid;
            gap: 12px;
            margin-bottom: 24px;
        }
        .med-card {
            background: #fafafa;
            border: 1px solid #ececec;
            border-radius: 16px;
            padding: 14px 16px;
        }
        .med-name {
            font-weight: bold;
            margin-bottom: 6px;
        }
        .med-frequency {
            color: #4b5563;
        }
        .med-warning {
            color: #8b5e00;
            font-size: 0.92rem;
            margin-top: 4px;
        }

        .calendar-wrap {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            align-items: start;
        }
        .calendar-card {
            background: #fafafa;
            border: 1px solid #ececec;
            border-radius: 16px;
            padding: 16px;
        }
        .calendar-title {
            font-weight: bold;
            margin-bottom: 12px;
        }
        .calendar-header, .calendar-grid {
            display: grid;
            grid-template-columns: repeat(7, 1fr);
            gap: 8px;
        }
        .dow {
            font-size: 0.8rem;
            color: #6b7280;
            text-align: center;
            font-weight: bold;
        }
        .date-cell {
            min-height: 48px;
            border-radius: 12px;
            background: #e7ebf0;
            border: 1px solid transparent;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            font-weight: bold;
        }
        .date-cell.blank {
            background: transparent;
            cursor: default;
        }
        .date-cell.selected {
            border-color: #111;
            background: #dce9ff;
        }
        .date-cell.note {
            background: #fff3cd;
        }
        .selected-day-panel {
            margin-top: 20px;
            background: #fafafa;
            border: 1px solid #ececec;
            border-radius: 16px;
            padding: 16px;
        }
        .selected-title {
            font-weight: bold;
            margin-bottom: 12px;
        }
        .selected-list {
            display: grid;
            gap: 10px;
        }
        .selected-item {
            padding: 10px 12px;
            background: white;
            border: 1px solid #ececec;
            border-radius: 12px;
        }
        .selected-note {
            color: #6b7280;
            margin-top: 10px;
            line-height: 1.5;
        }

        @media (max-width: 1100px) {
            .page {
                grid-template-columns: 1fr;
            }
        }
        @media (max-width: 820px) {
            .calendar-wrap {
                grid-template-columns: 1fr;
            }
        }
        @media (max-width: 700px) {
            .week-row {
                grid-template-columns: repeat(4, 1fr);
            }
        }
        @media (max-width: 520px) {
            .week-row {
                grid-template-columns: repeat(2, 1fr);
            }
            .buddy-card {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <div class="page">
        <aside class="sidebar">
            <img class="profile-image" src="{{ profile_image_url }}" alt="Allen profile picture">
            <div class="welcome">welcome back, allen</div>
            <div class="affirmation">{{ affirmation }}</div>
        </aside>

        <main class="main-card">
            <div class="title">Allen's Progress Indicator</div>

            <div class="tabs">
                <button class="tab active" onclick="showTab('progress')">Progress</button>
                <button class="tab" onclick="showTab('buddies')">Buddies</button>
                <button class="tab" onclick="showTab('medication')">Medication</button>
            </div>

            <div id="progress" class="tab-content active">
                <div class="section-title">Progress</div>
                <div id="weeks-container"></div>
                <div class="action-row">
                    <button class="main-button" onclick="createNewWeek()">New Week</button>
                    <div class="timestamp" id="updated"></div>
                </div>
            </div>

            <div id="buddies" class="tab-content">
                <div class="section-title">Buddies</div>
                <div class="buddies-list" id="buddies-list"></div>
            </div>

            <div id="medication" class="tab-content">
                <div class="section-title">Medication</div>
                <div class="med-list" id="med-list"></div>

                <div class="calendar-wrap">
                    <div class="calendar-card">
                        <div class="calendar-title">April 2026</div>
                        <div class="calendar-header" id="april-header"></div>
                        <div class="calendar-grid" id="april-grid"></div>
                    </div>

                    <div class="calendar-card">
                        <div class="calendar-title">May 2026</div>
                        <div class="calendar-header" id="may-header"></div>
                        <div class="calendar-grid" id="may-grid"></div>
                    </div>
                </div>

                <div class="selected-day-panel">
                    <div class="selected-title" id="selected-date-title">Click a date</div>
                    <div class="selected-list" id="selected-day-list"></div>
                    <div class="selected-note">
                        Only schedules you explicitly gave are treated as fixed here. Items marked “Confirm schedule” should be verified with Allen’s prescriber, pharmacist, or medication list before using this as a medication plan.
                    </div>
                </div>
            </div>
        </main>
    </div>

    <script>
        let latestMedicationData = [];

        function showTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));

            document.getElementById(tabId).classList.add('active');

            const tabs = document.querySelectorAll('.tab');
            if (tabId === 'progress') tabs[0].classList.add('active');
            if (tabId === 'buddies') tabs[1].classList.add('active');
            if (tabId === 'medication') tabs[2].classList.add('active');
        }

        function renderWeeks(weeks) {
            const container = document.getElementById('weeks-container');
            container.innerHTML = '';

            weeks.forEach((week, index) => {
                const block = document.createElement('div');
                block.className = 'week-block';

                const label = document.createElement('div');
                label.className = 'week-label';
                label.textContent = index === weeks.length - 1 ? 'Current Week' : `Week ${index + 1}`;
                block.appendChild(label);

                const row = document.createElement('div');
                row.className = 'week-row';

                week.readings.forEach(item => {
                    const box = document.createElement('div');
                    box.className = 'day-box ' + (item.correct ? 'green' : 'gray');
                    box.innerHTML = `
                        <div class="day-name">${item.day.slice(0, 3)}</div>
                        <div class="day-status">${item.correct ? 'Medication taken' : 'Not yet'}</div>
                    `;
                    row.appendChild(box);
                });

                block.appendChild(row);
                container.appendChild(block);
            });
        }

        function renderBuddies(buddies) {
            const container = document.getElementById('buddies-list');
            container.innerHTML = '';

            buddies.forEach(buddy => {
                const card = document.createElement('div');
                card.className = 'buddy-card';

                const left = document.createElement('div');
                left.innerHTML = `
                    <div class="buddy-name">${buddy.name}</div>
                    <div class="buddy-metrics">
                        ${buddy.today_percent}% Today<br>
                        ${buddy.month_percent}% This Month
                    </div>
                `;

                const grid = document.createElement('div');
                grid.className = 'mini-grid';

                buddy.grid.forEach(v => {
                    const cell = document.createElement('div');
                    cell.className = 'mini-cell' + (v ? ' on' : '');
                    grid.appendChild(cell);
                });

                card.appendChild(left);
                card.appendChild(grid);
                container.appendChild(card);
            });
        }

        function renderMedicationList(meds) {
            latestMedicationData = meds;
            const container = document.getElementById('med-list');
            container.innerHTML = '';

            meds.forEach(med => {
                const card = document.createElement('div');
                card.className = 'med-card';

                const needsConfirm = med.frequency === 'Confirm schedule';

                card.innerHTML = `
                    <div class="med-name">${med.name}</div>
                    <div class="med-frequency">${med.frequency}</div>
                    ${needsConfirm ? '<div class="med-warning">Schedule not entered yet.</div>' : ''}
                `;

                container.appendChild(card);
            });
        }

        function renderCalendarHeader(targetId) {
            const target = document.getElementById(targetId);
            target.innerHTML = '';
            ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].forEach(d => {
                const el = document.createElement('div');
                el.className = 'dow';
                el.textContent = d;
                target.appendChild(el);
            });
        }

        function makeCalendarCells(year, monthIndex, targetId) {
            const target = document.getElementById(targetId);
            target.innerHTML = '';

            const first = new Date(year, monthIndex, 1);
            const last = new Date(year, monthIndex + 1, 0);

            const startBlankCount = first.getDay();

            for (let i = 0; i < startBlankCount; i++) {
                const blank = document.createElement('div');
                blank.className = 'date-cell blank';
                target.appendChild(blank);
            }

            for (let day = 1; day <= last.getDate(); day++) {
                const btn = document.createElement('button');
                btn.className = 'date-cell';
                btn.textContent = day;
                btn.type = 'button';

                const iso = `${year}-${String(monthIndex + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
                btn.dataset.iso = iso;

                // Highlight only dates with explicit info we know
                if (iso === '2026-06-04') {
                    btn.classList.add('note');
                }

                btn.addEventListener('click', () => selectDate(iso, btn));
                target.appendChild(btn);
            }
        }

        function selectDate(iso, selectedButton) {
            document.querySelectorAll('.date-cell.selected').forEach(el => el.classList.remove('selected'));
            selectedButton.classList.add('selected');

            const title = document.getElementById('selected-date-title');
            const list = document.getElementById('selected-day-list');

            title.textContent = `Medication guidance for ${iso}`;
            list.innerHTML = '';

            latestMedicationData.forEach(med => {
                let detail = med.frequency;

                if (med.kind === 'unspecified') {
                    detail = 'Confirm schedule before relying on this calendar.';
                } else if (med.kind === 'prn') {
                    detail = 'As needed.';
                } else if (med.kind === 'weekly') {
                    detail = 'Once weekly — weekly day not entered yet.';
                } else if (med.kind === 'injection') {
                    detail = 'Every 6 months. Next expected date: 2026-06-04.';
                }

                const item = document.createElement('div');
                item.className = 'selected-item';
                item.innerHTML = `
                    <strong>${med.name}</strong><br>
                    ${detail}
                `;
                list.appendChild(item);
            });
        }

        async function refreshStatus() {
            const response = await fetch('/status');
            const data = await response.json();

            renderWeeks(data.weeks);
            renderBuddies(data.buddies);
            renderMedicationList(data.medications);

            renderCalendarHeader('april-header');
            renderCalendarHeader('may-header');
            makeCalendarCells(2026, 3, 'april-grid');
            makeCalendarCells(2026, 4, 'may-grid');

            document.getElementById('updated').textContent =
                'Last updated: ' + data.timestamp;
        }

        async function createNewWeek() {
            await fetch('/new_week', { method: 'POST' });
            refreshStatus();
        }

        refreshStatus();
        setInterval(refreshStatus, 500);
    </script>
</body>
</html>
"""


@app.route("/")
def dashboard():
    return render_template_string(
        HTML,
        profile_image_url="/static/allen_profile.jpg",
        affirmation=get_daily_affirmation(),
    )


@app.route("/status")
def status():
    weeks = ensure_current_week()

    with lock:
        response_weeks = []

        for i, week in enumerate(weeks):
            is_current = (i == len(weeks) - 1)
            readings = []

            for day in DISPLAY_DAYS:
                correct = sensor_data[day]["correct"] if is_current else week["days"].get(day, False)
                readings.append({
                    "day": day,
                    "correct": correct
                })

            response_weeks.append({
                "created_at": week["created_at"],
                "readings": readings
            })

    return jsonify({
        "timestamp": now_str(),
        "weeks": response_weeks,
        "buddies": fake_buddies_data(),
        "medications": medication_data(),
        "mqtt": mqtt_status,
    })


@app.route("/new_week", methods=["POST"])
def new_week():
    with lock:
        create_new_week()
        reset_in_memory_for_new_week()
    publish_new_week_command()
    return jsonify({"ok": True})


if __name__ == "__main__":
    start_mqtt()
    app.run(host="0.0.0.0", port=5000, debug=False)
