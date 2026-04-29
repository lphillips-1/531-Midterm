import random
import time
import json
import tkinter as tk
import urllib.parse
import urllib.request
import argparse
from tkinter import ttk, messagebox
from datetime import datetime, timedelta
from paho.mqtt import client as mqtt_client
from reed_backend import GPIO, REED_MAP, get_reed_status, reset_reed_status

REPORT_INTERVAL_SECONDS = 5
TOTAL_RUNTIME_SECONDS = 90
MQTT_BROKER = "localhost"
MQTT_PORT = 1883
MQTT_TOPIC = "sensor/daily_bit"
MQTT_CLIENT_ID = f"daily-bit-sim-{int(time.time())}"

WEEKDAY_TO_INDEX = {
    "Monday": 0,
    "Tuesday": 1,
    "Wednesday": 2,
    "Thursday": 3,
    "Friday": 4,
    "Saturday": 5,
    "Sunday": 6,
}


def query_openfda_medications(search_term: str = "", limit: int = 8):
    api_key = ""
    try:
        with open(".env", "r", encoding="utf-8") as env_file:
            for line in env_file:
                text = line.strip()
                if text.startswith("OPENFDA_API_KEY="):
                    api_key = text.split("=", 1)[1].strip()
                    break
    except FileNotFoundError:
        pass

    params = {"limit": max(limit, 5)}
    if api_key:
        params["api_key"] = api_key

    if search_term.strip():
        q = search_term.strip()
        params["search"] = f'(openfda.brand_name:"{q}*"+openfda.generic_name:"{q}*")'
        params["limit"] = max(limit, 10)
        url = f"https://api.fda.gov/drug/label.json?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        seen = set()
        options = []
        for rec in payload.get("results", []):
            openfda = rec.get("openfda", {})
            for key in ("brand_name", "generic_name"):
                for name in openfda.get(key, []):
                    candidate = str(name).strip()
                    if not candidate:
                        continue
                    lowered = candidate.lower()
                    if lowered in seen:
                        continue
                    if q.lower() not in lowered:
                        continue
                    seen.add(lowered)
                    options.append(candidate)
                    if len(options) >= limit:
                        return options
        return options

    params = {"count": "openfda.brand_name.exact", "limit": limit}
    if api_key:
        params["api_key"] = api_key
    url = f"https://api.fda.gov/drug/label.json?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return [str(item.get("term", "")).strip() for item in payload.get("results", []) if item.get("term")]


def run_bluetooth_sync_animation():
    state = {"done": False}

    win = tk.Tk()
    win.title("SyncWellness Sensor Bluetooth")
    win.geometry("520x300")
    win.configure(bg="#f4f6f8")

    title = tk.Label(
        win,
        text="SyncWellness sensor bluetooth",
        font=("Segoe UI", 16, "bold"),
        bg="#f4f6f8",
        fg="#111827",
    )
    title.pack(pady=(20, 10))

    status_var = tk.StringVar(value="Looking for nearby bluetooth devices")
    status = tk.Label(
        win,
        textvariable=status_var,
        font=("Segoe UI", 11),
        bg="#f4f6f8",
        fg="#374151",
    )
    status.pack(pady=(4, 12))

    progress_var = tk.StringVar(value="")
    progress = tk.Label(
        win,
        textvariable=progress_var,
        font=("Segoe UI", 22, "bold"),
        bg="#f4f6f8",
        fg="#2563eb",
    )
    progress.pack(pady=8)

    found_var = tk.StringVar(value="")
    found_label = tk.Label(
        win,
        textvariable=found_var,
        font=("Segoe UI", 12, "bold"),
        bg="#f4f6f8",
        fg="#0f766e",
    )
    found_label.pack(pady=(8, 10))

    check_var = tk.StringVar(value="")
    check = tk.Label(
        win,
        textvariable=check_var,
        font=("Segoe UI", 46, "bold"),
        bg="#f4f6f8",
        fg="#16a34a",
    )
    check.pack(pady=(4, 0))

    def animate_dots(step=0):
        if state["done"]:
            return
        dots = "." * ((step % 3) + 1)
        progress_var.set(f"Scanning{dots}")
        win.after(300, lambda: animate_dots(step + 1))

    def show_found():
        status_var.set("Device found")
        progress_var.set("")
        found_var.set("SW-Weekly vitamin planner")

    def show_success():
        status_var.set("Bluetooth sync successful")
        check_var.set("✓")
        state["done"] = True
        win.after(1000, win.destroy)

    animate_dots()
    win.after(2300, show_found)
    win.after(3600, show_success)
    win.mainloop()
    return True


def collect_permissions_setup():
    data = {
        "invites": [],
        "synced_contacts": [],
    }

    favorite_contacts = ["Daughter", "Mother", "Father", "Son", "Partner", "Caregiver"]

    win = tk.Tk()
    win.title("Permissions")
    win.geometry("760x620")
    win.configure(bg="#f4f6f8")

    tk.Label(
        win,
        text="Permissions",
        font=("Segoe UI", 16, "bold"),
        bg="#f4f6f8",
        fg="#111827",
    ).pack(pady=(14, 6))

    tk.Label(
        win,
        text="Add people who can view your medication logging information (invite is simulated).",
        font=("Segoe UI", 10),
        bg="#f4f6f8",
        fg="#374151",
    ).pack(pady=(0, 10))

    invite_frame = tk.LabelFrame(
        win,
        text="Invite by name and phone",
        bg="#f4f6f8",
        fg="#111827",
        font=("Segoe UI", 10, "bold"),
        padx=10,
        pady=10,
    )
    invite_frame.pack(fill="x", padx=20, pady=6)

    invite_name_var = tk.StringVar()
    invite_phone_var = tk.StringVar()
    invite_status_var = tk.StringVar(value="No SMS is sent. This is a demo invite list.")

    tk.Label(invite_frame, text="Name", bg="#f4f6f8").grid(row=0, column=0, sticky="w")
    tk.Entry(invite_frame, textvariable=invite_name_var, width=24).grid(row=0, column=1, padx=8, pady=4, sticky="w")
    tk.Label(invite_frame, text="Phone", bg="#f4f6f8").grid(row=0, column=2, sticky="w")
    tk.Entry(invite_frame, textvariable=invite_phone_var, width=20).grid(row=0, column=3, padx=8, pady=4, sticky="w")

    invite_list = tk.Listbox(invite_frame, width=92, height=6)
    invite_list.grid(row=1, column=0, columnspan=4, pady=8, sticky="w")

    def add_invite():
        name = invite_name_var.get().strip()
        phone = invite_phone_var.get().strip()
        if not name or not phone:
            messagebox.showwarning("Missing fields", "Enter both name and phone number.")
            return
        entry = {"name": name, "phone": phone, "status": "invite_queued_demo"}
        data["invites"].append(entry)
        invite_list.insert(tk.END, f"{name} ({phone}) - invite queued (demo)")
        invite_name_var.set("")
        invite_phone_var.set("")
        invite_status_var.set(f"Invite added for {name}.")

    tk.Button(
        invite_frame,
        text="Add Invite",
        command=add_invite,
        bg="#2563eb",
        fg="#ffffff",
        relief="flat",
        padx=10,
        pady=5,
    ).grid(row=2, column=0, pady=(0, 4), sticky="w")

    tk.Label(invite_frame, textvariable=invite_status_var, bg="#f4f6f8", fg="#4b5563").grid(
        row=2, column=1, columnspan=3, sticky="w", padx=8
    )

    contacts_frame = tk.LabelFrame(
        win,
        text="Synced Contacts (favorites)",
        bg="#f4f6f8",
        fg="#111827",
        font=("Segoe UI", 10, "bold"),
        padx=10,
        pady=10,
    )
    contacts_frame.pack(fill="x", padx=20, pady=8)

    button_row = tk.Frame(contacts_frame, bg="#f4f6f8")
    button_row.pack(anchor="w", pady=(0, 6))

    synced_list = tk.Listbox(contacts_frame, width=92, height=6)
    synced_list.pack(anchor="w")

    def add_favorite_contact(label):
        if label in data["synced_contacts"]:
            return
        data["synced_contacts"].append(label)
        synced_list.insert(tk.END, f"{label} - synced")

    for label in favorite_contacts:
        tk.Button(
            button_row,
            text=f"Add {label}",
            command=lambda v=label: add_favorite_contact(v),
            bg="#0f766e",
            fg="#ffffff",
            relief="flat",
            padx=8,
            pady=4,
        ).pack(side="left", padx=4)

    controls = tk.Frame(win, bg="#f4f6f8")
    controls.pack(fill="x", padx=20, pady=12)

    result = {"submitted": False}

    def continue_flow():
        result["submitted"] = True
        win.destroy()

    def cancel_flow():
        result["submitted"] = False
        win.destroy()

    tk.Button(controls, text="Cancel", command=cancel_flow, bg="#e5e7eb", relief="flat", padx=12, pady=6).pack(
        side="right", padx=6
    )
    tk.Button(
        controls,
        text="Continue",
        command=continue_flow,
        bg="#111827",
        fg="#ffffff",
        relief="flat",
        padx=12,
        pady=6,
    ).pack(side="right", padx=6)

    win.mainloop()
    if not result["submitted"]:
        return None
    return data


def collect_medication_selection():
    selected = {"value": None}

    win = tk.Tk()
    win.title("Medication Selection")
    win.geometry("720x500")
    win.configure(bg="#f4f6f8")

    tk.Label(
        win,
        text="Select Your Medication",
        font=("Segoe UI", 16, "bold"),
        bg="#f4f6f8",
        fg="#111827",
    ).pack(pady=(16, 6))

    tk.Label(
        win,
        text="Choose from OpenFDA results before moving to permissions.",
        font=("Segoe UI", 10),
        bg="#f4f6f8",
        fg="#374151",
    ).pack(pady=(0, 10))

    search_frame = tk.Frame(win, bg="#f4f6f8")
    search_frame.pack(fill="x", padx=20, pady=6)

    query_var = tk.StringVar()
    tk.Entry(search_frame, textvariable=query_var, width=36).pack(side="left", padx=(0, 8))

    results_list = tk.Listbox(win, width=76, height=14)
    results_list.pack(padx=20, pady=8)

    status_var = tk.StringVar(value="Loading popular medications...")
    tk.Label(win, textvariable=status_var, bg="#f4f6f8", fg="#4b5563").pack(pady=(0, 8))

    def load_results(term=""):
        try:
            options = query_openfda_medications(search_term=term, limit=8)
            results_list.delete(0, tk.END)
            for item in options:
                results_list.insert(tk.END, item)
            if options:
                status_var.set(f"Loaded {len(options)} medication options.")
            else:
                status_var.set("No medications found. Try another search.")
        except Exception as exc:
            status_var.set(f"OpenFDA lookup failed: {exc}")

    def search_now():
        load_results(query_var.get().strip())

    tk.Button(
        search_frame,
        text="Search OpenFDA",
        command=search_now,
        bg="#2563eb",
        fg="#ffffff",
        relief="flat",
        padx=10,
        pady=5,
    ).pack(side="left")

    controls = tk.Frame(win, bg="#f4f6f8")
    controls.pack(fill="x", padx=20, pady=10)

    def continue_flow():
        selected_idx = results_list.curselection()
        if not selected_idx:
            messagebox.showwarning("Select medication", "Select one medication from the list.")
            return
        selected["value"] = results_list.get(selected_idx[0])
        win.destroy()

    def cancel_flow():
        selected["value"] = None
        win.destroy()

    tk.Button(controls, text="Cancel", command=cancel_flow, bg="#e5e7eb", relief="flat", padx=12, pady=6).pack(
        side="right", padx=6
    )
    tk.Button(
        controls,
        text="Continue",
        command=continue_flow,
        bg="#111827",
        fg="#ffffff",
        relief="flat",
        padx=12,
        pady=6,
    ).pack(side="right", padx=6)

    load_results("")
    win.mainloop()
    return selected["value"]


def collect_subscriber_request_setup():
    data = {
        "accepted_invite": False,
        "requested_contacts": [],
    }

    contact_options = [
        "Daughter - Maya",
        "Mother - Elena",
        "Father - Robert",
        "Brother - Chris",
        "Partner - Sam",
        "Caregiver - Alex",
    ]

    win = tk.Tk()
    win.title("Request Access")
    win.geometry("720x560")
    win.configure(bg="#f4f6f8")

    tk.Label(
        win,
        text="View Main User Screen",
        font=("Segoe UI", 16, "bold"),
        bg="#f4f6f8",
        fg="#111827",
    ).pack(pady=(16, 6))

    tk.Label(
        win,
        text="Choose one of the options below to request medication logging access.",
        font=("Segoe UI", 10),
        bg="#f4f6f8",
        fg="#374151",
    ).pack(pady=(0, 12))

    invite_frame = tk.LabelFrame(
        win,
        text="Option 1: Accept Invite",
        bg="#f4f6f8",
        fg="#111827",
        font=("Segoe UI", 10, "bold"),
        padx=10,
        pady=10,
    )
    invite_frame.pack(fill="x", padx=20, pady=8)

    invite_status_var = tk.StringVar(value="No invite accepted yet.")

    def accept_invite():
        data["accepted_invite"] = True
        invite_status_var.set("Invite accepted. Access request marked as approved (demo).")

    tk.Button(
        invite_frame,
        text="Accept Invite",
        command=accept_invite,
        bg="#2563eb",
        fg="#ffffff",
        relief="flat",
        padx=12,
        pady=6,
    ).pack(anchor="w")

    tk.Label(invite_frame, textvariable=invite_status_var, bg="#f4f6f8", fg="#4b5563").pack(anchor="w", pady=(6, 0))

    contacts_frame = tk.LabelFrame(
        win,
        text="Option 2: Request from Contacts (demo list)",
        bg="#f4f6f8",
        fg="#111827",
        font=("Segoe UI", 10, "bold"),
        padx=10,
        pady=10,
    )
    contacts_frame.pack(fill="both", padx=20, pady=8, expand=True)

    contacts_list = tk.Listbox(contacts_frame, width=70, height=8, selectmode=tk.MULTIPLE)
    for contact in contact_options:
        contacts_list.insert(tk.END, contact)
    contacts_list.pack(anchor="w", pady=(0, 8))

    requested_log = tk.Listbox(contacts_frame, width=70, height=6)
    requested_log.pack(anchor="w")

    def request_selected_contacts():
        selected_indices = contacts_list.curselection()
        if not selected_indices:
            messagebox.showinfo("No contact selected", "Select one or more contacts to request access from.")
            return
        for idx in selected_indices:
            contact = contacts_list.get(idx)
            if contact not in data["requested_contacts"]:
                data["requested_contacts"].append(contact)
                requested_log.insert(tk.END, f"{contact} - request sent (demo)")

    tk.Button(
        contacts_frame,
        text="Request Access From Selected",
        command=request_selected_contacts,
        bg="#0f766e",
        fg="#ffffff",
        relief="flat",
        padx=12,
        pady=6,
    ).pack(anchor="w", pady=(8, 0))

    controls = tk.Frame(win, bg="#f4f6f8")
    controls.pack(fill="x", padx=20, pady=12)

    result = {"submitted": False}

    def continue_flow():
        result["submitted"] = True
        win.destroy()

    def cancel_flow():
        result["submitted"] = False
        win.destroy()

    tk.Button(controls, text="Cancel", command=cancel_flow, bg="#e5e7eb", relief="flat", padx=12, pady=6).pack(
        side="right", padx=6
    )
    tk.Button(
        controls,
        text="Continue",
        command=continue_flow,
        bg="#111827",
        fg="#ffffff",
        relief="flat",
        padx=12,
        pady=6,
    ).pack(side="right", padx=6)

    win.mainloop()
    if not result["submitted"]:
        return None
    return data


def collect_self_setup():
    data = {
        "username": "",
        "birthday": "",
        "health_profile": [],
    }

    condition_choices = [
        "Diabetes",
        "Hypertension",
        "Asthma",
        "Depression",
        "Anxiety",
        "Arthritis",
        "Other",
    ]

    win = tk.Tk()
    win.title("Account Setup - For Myself")
    win.geometry("760x640")
    win.configure(bg="#f4f6f8")

    tk.Label(
        win,
        text="Personal Setup",
        font=("Segoe UI", 16, "bold"),
        bg="#f4f6f8",
        fg="#111827",
    ).pack(pady=(14, 8))

    form = tk.Frame(win, bg="#f4f6f8")
    form.pack(fill="x", padx=20, pady=6)

    tk.Label(form, text="Username", bg="#f4f6f8", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
    username_var = tk.StringVar()
    tk.Entry(form, textvariable=username_var, width=36).grid(row=0, column=1, sticky="w", padx=10, pady=4)

    tk.Label(form, text="Birthday (YYYY-MM-DD)", bg="#f4f6f8", font=("Segoe UI", 10, "bold")).grid(
        row=1, column=0, sticky="w"
    )
    birthday_var = tk.StringVar()
    tk.Entry(form, textvariable=birthday_var, width=36).grid(row=1, column=1, sticky="w", padx=10, pady=4)

    hp_frame = tk.LabelFrame(
        win,
        text="Health Profile (diagnosed conditions + date diagnosed)",
        bg="#f4f6f8",
        fg="#111827",
        font=("Segoe UI", 10, "bold"),
        padx=10,
        pady=10,
    )
    hp_frame.pack(fill="x", padx=20, pady=8)

    condition_var = tk.StringVar()
    condition_combo = ttk.Combobox(hp_frame, textvariable=condition_var, values=condition_choices, width=22)
    condition_combo.grid(row=0, column=0, padx=4, pady=4, sticky="w")
    condition_custom_var = tk.StringVar()
    tk.Entry(hp_frame, textvariable=condition_custom_var, width=22).grid(row=0, column=1, padx=4, pady=4, sticky="w")
    diagnosed_date_var = tk.StringVar()
    tk.Entry(hp_frame, textvariable=diagnosed_date_var, width=16).grid(row=0, column=2, padx=4, pady=4, sticky="w")
    tk.Label(hp_frame, text="Date (YYYY-MM-DD)", bg="#f4f6f8").grid(row=0, column=3, padx=4, pady=4, sticky="w")

    health_list = tk.Listbox(hp_frame, width=90, height=5)
    health_list.grid(row=1, column=0, columnspan=4, padx=4, pady=6, sticky="w")

    def add_condition():
        picked = condition_var.get().strip()
        custom = condition_custom_var.get().strip()
        final_condition = custom if (picked == "Other" or not picked) else picked
        date_str = diagnosed_date_var.get().strip()
        if not final_condition:
            messagebox.showwarning("Missing field", "Enter or select a diagnosed condition.")
            return
        entry = {"condition": final_condition, "date_diagnosed": date_str}
        data["health_profile"].append(entry)
        health_list.insert(tk.END, f"{final_condition} - diagnosed: {date_str or 'n/a'}")
        condition_var.set("")
        condition_custom_var.set("")
        diagnosed_date_var.set("")

    tk.Button(hp_frame, text="Add Condition", command=add_condition, bg="#0f766e", fg="#ffffff", relief="flat").grid(
        row=2, column=0, pady=4, sticky="w"
    )

    controls = tk.Frame(win, bg="#f4f6f8")
    controls.pack(fill="x", padx=20, pady=10)

    def submit():
        username = username_var.get().strip()
        birthday = birthday_var.get().strip()
        if not username:
            messagebox.showwarning("Missing field", "Username is required.")
            return
        data["username"] = username
        data["birthday"] = birthday
        win.destroy()

    def cancel():
        data.clear()
        win.destroy()

    tk.Button(controls, text="Cancel", command=cancel, bg="#e5e7eb", relief="flat", padx=12, pady=6).pack(
        side="right", padx=6
    )
    tk.Button(controls, text="Continue", command=submit, bg="#111827", fg="#ffffff", relief="flat", padx=12, pady=6).pack(
        side="right", padx=6
    )

    win.mainloop()
    return data if data else None


def choose_account_mode():
    selection = {"mode": None}

    root = tk.Tk()
    root.title("Setup")
    root.geometry("420x220")
    root.configure(bg="#f4f6f8")

    tk.Label(
        root,
        text="Who is this account for?",
        font=("Segoe UI", 14, "bold"),
        bg="#f4f6f8",
        fg="#111827",
    ).pack(pady=(24, 12))

    button_frame = tk.Frame(root, bg="#f4f6f8")
    button_frame.pack(pady=8)

    def set_mode(mode):
        selection["mode"] = mode
        root.destroy()

    tk.Button(
        button_frame,
        text="For myself",
        width=18,
        command=lambda: set_mode("for_myself"),
        bg="#2563eb",
        fg="#ffffff",
        relief="flat",
        padx=8,
        pady=8,
    ).pack(side="left", padx=8)

    tk.Button(
        button_frame,
        text="Subscribe to someone else",
        width=24,
        command=lambda: set_mode("subscribe_someone_else"),
        bg="#0f766e",
        fg="#ffffff",
        relief="flat",
        padx=8,
        pady=8,
    ).pack(side="left", padx=8)

    root.mainloop()
    return selection["mode"]


def connect_mqtt(broker=MQTT_BROKER, port=MQTT_PORT, client_id=MQTT_CLIENT_ID):
    client = mqtt_client.Client(
        callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2,
        client_id=client_id,
    )
    client.connect(broker, port)
    client.loop_start()
    return client


def week_date_for_day(day_name: str, reference: datetime | None = None) -> datetime:
    now = reference or datetime.now()
    week_start = now - timedelta(days=now.weekday())
    offset = WEEKDAY_TO_INDEX[day_name]
    return week_start + timedelta(days=offset)


def publish_reed_switch_events(
    account_mode="for_myself",
    account_setup=None,
    broker=MQTT_BROKER,
    port=MQTT_PORT,
    topic=MQTT_TOPIC,
    poll_interval=0.2,
    reset_progress_on_start=False,
):
    client = connect_mqtt(broker=broker, port=port, client_id=f"reed-switch-publisher-{int(time.time())}")
    published_days = set()

    if reset_progress_on_start:
        reset_reed_status()

    print("Publishing real reed-switch completion events.")
    print(f"Watching days: {', '.join(REED_MAP.keys())}")

    try:
        while True:
            status = get_reed_status()

            for day_name, item in status.items():
                if not item["correct"] or day_name in published_days:
                    continue

                event_time = week_date_for_day(day_name).replace(
                    hour=datetime.now().hour,
                    minute=datetime.now().minute,
                    second=datetime.now().second,
                    microsecond=0,
                )
                payload = {
                    "day_name": day_name,
                    "simulated_time": event_time.isoformat(),
                    "value": 1,
                    "account_mode": account_mode,
                    "account_setup": account_setup or {},
                    "source": "reed_switch",
                    "reed_state": item["state"],
                    "reed_progress": item["progress"],
                }

                result = client.publish(topic, json.dumps(payload))
                status_text = (
                    "published"
                    if result.rc == mqtt_client.MQTT_ERR_SUCCESS
                    else f"publish_failed_rc_{result.rc}"
                )
                print(
                    f"{day_name}: completion detected -> {event_time:%Y-%m-%d %I:%M %p} [{status_text}]"
                )
                published_days.add(day_name)

            if len(published_days) == len(REED_MAP):
                print("All weekly reed-switch completion events have been published.")
                break

            time.sleep(poll_interval)
    finally:
        client.loop_stop()
        client.disconnect()


def run_simulation(
    report_interval=REPORT_INTERVAL_SECONDS,
    total_runtime=TOTAL_RUNTIME_SECONDS,
    seed=None,
    account_mode="for_myself",
    account_setup=None,
    broker=MQTT_BROKER,
    port=MQTT_PORT,
    topic=MQTT_TOPIC,
):
    if seed is not None:
        random.seed(seed)

    client = connect_mqtt(broker=broker, port=port)
    start_time = time.time()
    simulated_day = datetime.now().replace(hour=2, minute=0, second=0, microsecond=0)
    day_count = 0

    try:
        while True:
            elapsed = time.time() - start_time
            if elapsed >= total_runtime:
                break

            day_count += 1
            value = 1 if random.random() < 0.8 else 0
            payload = {
                "day_index": day_count,
                "simulated_time": simulated_day.isoformat(),
                "value": value,
                "account_mode": account_mode,
                "account_setup": account_setup or {},
            }

            result = client.publish(topic, json.dumps(payload))
            status = "published" if result.rc == mqtt_client.MQTT_ERR_SUCCESS else f"publish_failed_rc_{result.rc}"
            print(f"Day {day_count:02d}: {simulated_day:%Y-%m-%d %I:%M %p} -> {value} [{status}]")

            simulated_day += timedelta(days=1)

            remaining = total_runtime - (time.time() - start_time)
            if remaining <= 0:
                break

            time.sleep(min(report_interval, remaining))
    finally:
        client.loop_stop()
        client.disconnect()

    print(f"\nStopped automatically after {total_runtime} seconds.")
    print(f"Total simulated days reported: {day_count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MQTT publisher for demo or live reed-switch completion events.")
    parser.add_argument("--broker", default=MQTT_BROKER)
    parser.add_argument("--port", type=int, default=MQTT_PORT)
    parser.add_argument("--topic", default=MQTT_TOPIC)
    parser.add_argument(
        "--source",
        choices=("auto", "simulation", "reed"),
        default="auto",
        help="Publishing source: random simulation, live reed switches, or auto-detect.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.2,
        help="Polling interval in seconds for reed-switch mode.",
    )
    parser.add_argument(
        "--reset-reed-on-start",
        action="store_true",
        help="Reset reed-switch progress before starting live monitoring.",
    )
    args = parser.parse_args()

    mode = choose_account_mode()
    if mode is None:
        print("Setup closed. Simulation not started.")
    else:
        setup_data = {}
        if mode == "for_myself":
            setup_data = collect_self_setup()
            if setup_data is None:
                print("Setup cancelled. Simulation not started.")
                raise SystemExit(0)
            run_bluetooth_sync_animation()
            medication_name = collect_medication_selection()
            if not medication_name:
                print("Medication selection cancelled. Simulation not started.")
                raise SystemExit(0)
            setup_data["medication_name"] = medication_name
            permissions_data = collect_permissions_setup()
            if permissions_data is None:
                print("Permissions setup cancelled. Simulation not started.")
                raise SystemExit(0)
            setup_data["permissions"] = permissions_data
        elif mode == "subscribe_someone_else":
            setup_data = collect_subscriber_request_setup()
            if setup_data is None:
                print("Request setup cancelled. Simulation not started.")
                raise SystemExit(0)

        publish_source = args.source
        if publish_source == "auto":
            publish_source = "reed" if GPIO is not None else "simulation"

        if publish_source == "reed" and GPIO is None:
            print("RPi.GPIO is unavailable on this machine. Falling back to random simulation.")
            publish_source = "simulation"

        print(f"Setup complete: {mode}")
        if publish_source == "reed":
            publish_reed_switch_events(
                account_mode=mode,
                account_setup=setup_data,
                broker=args.broker,
                port=args.port,
                topic=args.topic,
                poll_interval=args.poll_interval,
                reset_progress_on_start=args.reset_reed_on_start,
            )
        else:
            run_simulation(
                seed=42,
                account_mode=mode,
                account_setup=setup_data,
                broker=args.broker,
                port=args.port,
                topic=args.topic,
            )  # Remove seed for non-reproducible randomness
