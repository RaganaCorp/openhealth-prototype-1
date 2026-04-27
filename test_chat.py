import urllib.request
import urllib.error
import json
import sys

base = "http://127.0.0.1:8000"

# Get first patient
with urllib.request.urlopen(f"{base}/patients") as r:
    patients = json.loads(r.read())
patient_id = patients[0]["id"]

# Create session
req = urllib.request.Request(
    f"{base}/patients/{patient_id}/chat-sessions",
    data=b"{}",
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req) as r:
    session = json.loads(r.read())
session_id = session["chat_session_id"]

print(f"patient_id={patient_id}")
print(f"session_id={session_id}")

# Send chat message
req = urllib.request.Request(
    f"{base}/chat",
    data=json.dumps({"patient_id": patient_id, "chat_session_id": session_id, "message": "test"}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req) as r:
        print("SUCCESS", r.read().decode())
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", errors="replace")
    print(f"HTTP {e.code} {e.reason}")
    print(f"BODY: {body}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")
