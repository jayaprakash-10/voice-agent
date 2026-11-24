"""Checks each API key in .env by making one tiny API call. Never prints the keys."""
import os, urllib.request, urllib.error, base64

def load_env(path=".env"):
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip().strip('"')

def get(url, headers):
    req = urllib.request.Request(url, headers={"User-Agent": "key-check", **headers})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return f"error: {e}"

load_env()
e = os.environ.get
checks = {}
if e("TWILIO_ACCOUNT_SID") and e("TWILIO_AUTH_TOKEN"):
    auth = base64.b64encode(f"{e('TWILIO_ACCOUNT_SID')}:{e('TWILIO_AUTH_TOKEN')}".encode()).decode()
    checks["Twilio"] = get(f"https://api.twilio.com/2010-04-01/Accounts/{e('TWILIO_ACCOUNT_SID')}.json", {"Authorization": f"Basic {auth}"})
if e("PLIVO_AUTH_ID") and e("PLIVO_AUTH_TOKEN"):
    auth = base64.b64encode(f"{e('PLIVO_AUTH_ID')}:{e('PLIVO_AUTH_TOKEN')}".encode()).decode()
    checks["Plivo"] = get(f"https://api.plivo.com/v1/Account/{e('PLIVO_AUTH_ID')}/", {"Authorization": f"Basic {auth}"})
if e("DEEPGRAM_API_KEY"):
    checks["Deepgram"] = get("https://api.deepgram.com/v1/projects", {"Authorization": f"Token {e('DEEPGRAM_API_KEY')}"})
if e("GROQ_API_KEY"):
    checks["Groq"] = get("https://api.groq.com/openai/v1/models", {"Authorization": f"Bearer {e('GROQ_API_KEY')}"})
if e("CARTESIA_API_KEY"):
    checks["Cartesia"] = get("https://api.cartesia.ai/voices", {"X-API-Key": e("CARTESIA_API_KEY"), "Cartesia-Version": "2024-11-13"})

for name in ["Twilio", "Plivo", "Deepgram", "Groq", "Cartesia"]:
    s = checks.get(name, "missing in .env")
    print(f"{name:9} {'OK' if s == 200 else 'FAIL'}  ({s})")
