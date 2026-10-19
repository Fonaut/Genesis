import os
import urllib.request
import urllib.error

token = os.environ.get("GENESIS_BRIDGE_TOKEN")

if not token:
    print("No bridge token is set in this Terminal.")
    print("We'll set up a shared local token next.")
    raise SystemExit(1)

request = urllib.request.Request(
    "http://127.0.0.1:8765/health",
    headers={"Authorization": f"Bearer {token}"}
)

try:
    with urllib.request.urlopen(request) as response:
        print("Status:", response.status)
        print(response.read().decode())
except urllib.error.HTTPError as error:
    print("HTTP error:", error.code)
    print(error.read().decode())
