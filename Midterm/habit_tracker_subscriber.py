import argparse
import calendar
import json
import os
import queue
import threading
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from openai import OpenAI
from paho.mqtt import client as mqtt_client


DATA_FILE = Path("medication_log.json")
DEFAULT_OPENFDA_ENDPOINT = "https://api.fda.gov/drug/label.json"
DEFAULT_OPENAI_MODEL = "gpt-5-mini"
DEFAULT_OPENAI_BASE_URL = "https://litellm.oit.duke.edu/"


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_log(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}


def save_log(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def parse_payload(payload: str) -> tuple[str, int, str | None, str | None]:
    data = json.loads(payload)
    simulated_time = data.get("simulated_time", data.get("simulated-time"))
    value = data.get("value")
    account_setup = data.get("account_setup", {}) or {}
    username = account_setup.get("username") or data.get("username")
    medication_name = account_setup.get("medication_name") or data.get("medication_name")

    if simulated_time is None or value not in (0, 1):
        raise ValueError("Payload must include simulated_time (or simulated-time) and value (0/1).")

    date_key = datetime.fromisoformat(simulated_time).date().isoformat()
    return date_key, int(value), username, medication_name


def _first_text(value):
    if isinstance(value, list) and value:
        return str(value[0])
    if value is None:
        return "Not available"
    return str(value)


def get_openfda_drug_info(drug_name: str) -> dict:
    if not drug_name.strip():
        raise ValueError("Medication name is required.")

    query = f'(openfda.brand_name:"{drug_name}"+openfda.generic_name:"{drug_name}")'
    params = {
        "search": query,
        "limit": 1,
    }

    api_key = os.getenv("OPENFDA_API_KEY")
    if api_key:
        params["api_key"] = api_key

    url = f"{DEFAULT_OPENFDA_ENDPOINT}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))

    results = payload.get("results", [])
    if not results:
        raise ValueError(
            f"No OpenFDA label results for '{drug_name}'. Try exact brand or generic spelling."
        )

    rec = results[0]
    openfda = rec.get("openfda", {})

    facts = {
        "queried_name": drug_name,
        "brand_name": _first_text(openfda.get("brand_name")),
        "generic_name": _first_text(openfda.get("generic_name")),
        "manufacturer": _first_text(openfda.get("manufacturer_name")),
        "route": _first_text(openfda.get("route")),
        "substance": _first_text(openfda.get("substance_name")),
        "purpose": _first_text(rec.get("purpose")),
        "indications_and_usage": _first_text(rec.get("indications_and_usage")),
        "warnings": _first_text(rec.get("warnings")),
        "do_not_use": _first_text(rec.get("do_not_use")),
        "stop_use": _first_text(rec.get("stop_use")),
    }

    return facts


def summarize_with_openai(facts: dict) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return (
            "LLM summary unavailable because OPENAI_API_KEY is not set.\n\n"
            "Plain-language fallback:\n"
            f"Medication: {facts['brand_name']} ({facts['generic_name']})\n"
            f"Use: {facts['indications_and_usage']}\n"
            f"Warnings: {facts['warnings']}\n"
            "Safety note: Confirm medication guidance with your clinician or pharmacist."
        )

    prompt = (
        "You are a health education assistant. Create in-depth but clear recommendations and education "
        "using the medication label data below, with a calm and predictable tone. "
        "Help the user avoid surprises by setting practical expectations for possible mood and symptom changes "
        "while tracking this specific medication. "
        "Output must be a maximum of 30 words total. "
        "Focus on: likely effects timeline, what to monitor, and when to contact a clinician. "
        "Avoid jargon and do not add claims that are not supported by the provided data. "
        "Do not use bullets. End with: 'This is not medical advice.'\n\n"
        f"openFDA data:\n{json.dumps(facts, indent=2)}"
    )
    model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    base_url = os.getenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL)

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
    )

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.choices[0].message.content
    if isinstance(text, str) and text.strip():
        return text.strip()

    return "LLM response received but no text content was returned."


class HabitTrackerUI:
    def __init__(self, root: tk.Tk, habit_log: dict, data_file: Path):
        self.root = root
        self.habit_log = habit_log
        self.data_file = data_file
        self.event_queue: queue.Queue = queue.Queue()

        today = datetime.now()
        self.view_year = today.year
        self.view_month = today.month

        self.status_var = tk.StringVar(value="Connecting to MQTT broker...")
        self.simulated_day_var = tk.StringVar(value="No day yet")
        self.title_var = tk.StringVar(value="Medication Adherence")
        self.selected_medication_var = tk.StringVar(value="Medication: not selected in setup")

        self.root.title("Medication Adherence Calendar")
        self.root.geometry("1000x760")
        self.root.configure(bg="#f4f6f8")
        self.window_visible = False

        self._build_layout()
        self._initialize_simulated_day_display()
        self._draw_calendar()

    def _build_layout(self) -> None:
        header_frame = tk.Frame(self.root, bg="#f4f6f8")
        header_frame.pack(fill="x", padx=18, pady=(14, 4))

        title = tk.Label(
            header_frame,
            textvariable=self.title_var,
            font=("Segoe UI", 20, "bold"),
            bg="#f4f6f8",
            fg="#1f2937",
        )
        title.pack(side="left")

        day_display = tk.Label(
            header_frame,
            textvariable=self.simulated_day_var,
            font=("Segoe UI", 14, "bold"),
            bg="#f4f6f8",
            fg="#1d4ed8",
        )
        day_display.pack(side="right")

        controls = tk.Frame(self.root, bg="#f4f6f8")
        controls.pack(pady=(2, 8))

        tk.Button(
            controls,
            text="< Previous",
            command=self.prev_month,
            bg="#e5e7eb",
            fg="#111827",
            relief="flat",
            padx=12,
            pady=6,
        ).pack(side="left", padx=6)

        self.month_label = tk.Label(
            controls,
            text="",
            font=("Segoe UI", 14, "bold"),
            bg="#f4f6f8",
            fg="#111827",
            width=18,
        )
        self.month_label.pack(side="left", padx=8)

        tk.Button(
            controls,
            text="Next >",
            command=self.next_month,
            bg="#e5e7eb",
            fg="#111827",
            relief="flat",
            padx=12,
            pady=6,
        ).pack(side="left", padx=6)

        self.calendar_frame = tk.Frame(self.root, bg="#f4f6f8")
        self.calendar_frame.pack(fill="both", expand=True, padx=18, pady=(4, 8))

        legend = tk.Label(
            self.root,
            text="Legend: [x] medication taken   [ ] missed dose   blank = no data",
            font=("Segoe UI", 10),
            bg="#f4f6f8",
            fg="#4b5563",
        )
        legend.pack(pady=(4, 2))

        facts_frame = tk.Frame(self.root, bg="#f4f6f8")
        facts_frame.pack(fill="both", expand=False, padx=18, pady=(4, 8))

        tk.Label(
            facts_frame,
            text="Medication Facts (OpenFDA + LLM):",
            font=("Segoe UI", 11, "bold"),
            bg="#f4f6f8",
            fg="#111827",
        ).pack(anchor="w")

        tk.Label(
            facts_frame,
            textvariable=self.selected_medication_var,
            font=("Segoe UI", 10, "bold"),
            bg="#f4f6f8",
            fg="#1d4ed8",
        ).pack(anchor="w", pady=(2, 4))

        self.facts_text = tk.Text(
            facts_frame,
            height=10,
            wrap="word",
            font=("Segoe UI", 10),
            bg="#ffffff",
            fg="#111827",
            relief="solid",
            bd=1,
        )
        self.facts_text.pack(fill="x", pady=(4, 0))
        self.facts_text.insert(
            "1.0",
            "Medication is set during setup flow.\n"
            "Each MQTT adherence message triggers OpenFDA lookup and an LLM summary for that medication.",
        )

        status = tk.Label(
            self.root,
            textvariable=self.status_var,
            font=("Segoe UI", 10),
            bg="#f4f6f8",
            fg="#374151",
        )
        status.pack(pady=(0, 10))

    def _draw_calendar(self) -> None:
        for child in self.calendar_frame.winfo_children():
            child.destroy()

        self.month_label.config(text=f"{calendar.month_name[self.view_month]} {self.view_year}")

        weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        for col, day in enumerate(weekdays):
            tk.Label(
                self.calendar_frame,
                text=day,
                font=("Segoe UI", 10, "bold"),
                bg="#f4f6f8",
                fg="#111827",
                width=12,
            ).grid(row=0, column=col, padx=4, pady=4)

        cal = calendar.Calendar(firstweekday=6)
        weeks = cal.monthdayscalendar(self.view_year, self.view_month)

        for row, week in enumerate(weeks, start=1):
            for col, day in enumerate(week):
                card = tk.Frame(
                    self.calendar_frame,
                    bg="#ffffff",
                    bd=1,
                    relief="solid",
                    width=110,
                    height=74,
                )
                card.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")
                card.grid_propagate(False)

                if day == 0:
                    tk.Label(card, text="", bg="#ffffff").pack(expand=True)
                    continue

                date_key = f"{self.view_year:04d}-{self.view_month:02d}-{day:02d}"
                value = self.habit_log.get(date_key)

                day_label = tk.Label(
                    card,
                    text=f"{day:02d}",
                    font=("Segoe UI", 10, "bold"),
                    bg="#ffffff",
                    fg="#111827",
                )
                day_label.pack(anchor="nw", padx=6, pady=(5, 2))

                if value == 1:
                    mark_text = "[x] Taken"
                    mark_color = "#166534"
                    card.configure(bg="#dcfce7")
                    day_label.configure(bg="#dcfce7")
                elif value == 0:
                    mark_text = "[ ] Missed"
                    mark_color = "#b91c1c"
                    card.configure(bg="#fee2e2")
                    day_label.configure(bg="#fee2e2")
                else:
                    mark_text = ""
                    mark_color = "#6b7280"

                tk.Label(
                    card,
                    text=mark_text,
                    font=("Segoe UI", 10),
                    bg=card.cget("bg"),
                    fg=mark_color,
                ).pack(anchor="w", padx=6)

        for i in range(7):
            self.calendar_frame.grid_columnconfigure(i, weight=1)

    def _initialize_simulated_day_display(self) -> None:
        if not self.habit_log:
            return

        try:
            latest_date_key = max(self.habit_log.keys())
            latest_date = datetime.fromisoformat(latest_date_key)
            self.set_simulated_day_display(latest_date)
        except Exception:
            pass

    def set_simulated_day_display(self, date_value: datetime) -> None:
        self.simulated_day_var.set(f"{date_value.strftime('%A')} {date_value.day}")

    def set_user_title(self, username: str | None) -> None:
        if username and username.strip():
            title = f"{username.strip()}'s medication adherence"
        else:
            title = "Medication Adherence"
        self.title_var.set(title)
        self.root.title(title)

    def show_window(self) -> None:
        if not self.window_visible:
            self.root.deiconify()
            self.root.lift()
            self.window_visible = True

    def prev_month(self) -> None:
        if self.view_month == 1:
            self.view_month = 12
            self.view_year -= 1
        else:
            self.view_month -= 1
        self._draw_calendar()

    def next_month(self) -> None:
        if self.view_month == 12:
            self.view_month = 1
            self.view_year += 1
        else:
            self.view_month += 1
        self._draw_calendar()

    def queue_status(self, message: str) -> None:
        self.event_queue.put(("status", message))

    def queue_data_update(
        self,
        date_key: str,
        value: int,
        username: str | None = None,
        medication_name: str | None = None,
    ) -> None:
        self.event_queue.put(("data", date_key, value, username, medication_name))

    def queue_facts_result(self, text: str) -> None:
        self.event_queue.put(("facts", text))

    def set_selected_medication(self, medication_name: str | None) -> None:
        if medication_name and medication_name.strip():
            self.selected_medication_var.set(f"Medication: {medication_name.strip()}")
        else:
            self.selected_medication_var.set("Medication: not selected in setup")

    def _set_facts_text(self, text: str) -> None:
        self.facts_text.delete("1.0", tk.END)
        self.facts_text.insert("1.0", text)

    def fetch_facts_async(self, medication: str, source: str) -> None:
        def worker() -> None:
            try:
                facts = get_openfda_drug_info(medication)
                summary = summarize_with_openai(facts)
                details = (
                    f"Medication: {facts['brand_name']} ({facts['generic_name']})\n"
                    f"Route: {facts['route']}\n"
                    "\n"
                    f"Plain-language summary:\n{summary}"
                )
                self.queue_facts_result(details)
            except urllib.error.HTTPError as exc:
                self.queue_facts_result(f"OpenFDA request failed: HTTP {exc.code}")
            except Exception as exc:
                self.queue_facts_result(f"Medication info unavailable: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def process_events(self) -> None:
        while not self.event_queue.empty():
            event = self.event_queue.get_nowait()
            if event[0] == "status":
                self.status_var.set(event[1])
            elif event[0] == "data":
                _, date_key, value, username, medication_name = event
                self.show_window()
                self.habit_log[date_key] = value
                save_log(self.data_file, self.habit_log)

                updated_date = datetime.fromisoformat(date_key)
                self.set_user_title(username)
                self.set_selected_medication(medication_name)
                self.set_simulated_day_display(updated_date)
                self.status_var.set(
                    f"Updated {date_key}: {'medication taken' if value == 1 else 'dose missed'}"
                )

                if (
                    updated_date.year == self.view_year
                    and updated_date.month == self.view_month
                ):
                    self._draw_calendar()

                if medication_name:
                    self.fetch_facts_async(
                        medication_name,
                        source=f"MQTT event {date_key} ({'taken' if value == 1 else 'missed'})",
                    )
                else:
                    self.queue_facts_result(
                        "No medication found in setup payload. Complete setup medication selection first."
                    )
            elif event[0] == "facts":
                self._set_facts_text(event[1])

        self.root.after(150, self.process_events)


def start_subscriber_ui(
    broker: str,
    port: int,
    topic: str,
    data_file: Path,
    reset_on_start: bool = True,
) -> None:
    if reset_on_start:
        habit_log = {}
        save_log(data_file, habit_log)
    else:
        habit_log = load_log(data_file)

    root = tk.Tk()
    root.withdraw()
    ui = HabitTrackerUI(root=root, habit_log=habit_log, data_file=data_file)

    client = mqtt_client.Client(
        callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2,
        client_id="habit-tracker-subscriber",
    )

    def on_connect(_client, _userdata, _flags, reason_code, _properties):
        if reason_code == 0:
            ui.queue_status(f"Connected to {broker}:{port}. Subscribed to {topic}")
            _client.subscribe(topic)
        else:
            ui.queue_status(f"Connection failed. Reason code: {reason_code}")

    def on_message(_client, _userdata, msg):
        try:
            payload_text = msg.payload.decode("utf-8")
            date_key, value, username, medication_name = parse_payload(payload_text)
            ui.queue_data_update(
                date_key,
                value,
                username=username,
                medication_name=medication_name,
            )
        except Exception as exc:
            ui.queue_status(f"Message skipped: {exc}")

    client.on_connect = on_connect
    client.on_message = on_message

    def on_close() -> None:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    try:
        client.connect(broker, port)
        client.loop_start()
    except Exception as exc:
        ui.queue_status(f"Could not connect: {exc}")

    ui.process_events()
    root.mainloop()


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="MQTT medication adherence calendar UI subscriber.")
    parser.add_argument("--broker", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--topic", default="sensor/daily_bit")
    parser.add_argument("--data-file", default=str(DATA_FILE))
    parser.add_argument(
        "--keep-log",
        action="store_true",
        help="Keep existing calendar values from previous runs.",
    )
    args = parser.parse_args()

    start_subscriber_ui(
        broker=args.broker,
        port=args.port,
        topic=args.topic,
        data_file=Path(args.data_file),
        reset_on_start=not args.keep_log,
    )


if __name__ == "__main__":
    main()
