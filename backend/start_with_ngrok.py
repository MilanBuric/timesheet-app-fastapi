"""
One-command launcher for testing with people outside your network.

What this solves: running `uvicorn` and `ngrok` separately means copying
the new ngrok URL into .env's BASE_URL by hand every single time (free
ngrok URLs change on every restart), then restarting the server so it
picks up the change. Forget that step and every invite/reset-password
email goes out with a dead localhost link — which is exactly the bug
you hit earlier. This script does both steps together, in the right
order, every time.

One-time setup:
    pip install pyngrok
    ngrok config add-authtoken <your token>   (free at ngrok.com)

Usage:
    python start_with_ngrok.py

Then share the printed https://....ngrok-free.app URL with your testers.
Leave this running for the whole session — closing it tears down the
tunnel and the URL stops working.
"""
import os
import sys

try:
    from pyngrok import ngrok
except ImportError:
    print("pyngrok isn't installed. Run: pip install pyngrok")
    sys.exit(1)

import uvicorn


def main():
    port = int(os.environ.get("PORT", 8000))

    tunnel = ngrok.connect(port, "http")
    public_url = tunnel.public_url.replace("http://", "https://")

    # Must happen before uvicorn imports main.py (and, transitively,
    # email_utils.py) — load_dotenv() there does not override a variable
    # that's already set, so this takes priority over whatever .env has.
    os.environ["BASE_URL"] = public_url

    print("\n" + "=" * 60)
    print(f"  Public URL for testers:  {public_url}")
    print(f"  (emailed invite/reset links will use this address)")
    print("=" * 60 + "\n")

    try:
        uvicorn.run("main:app", host="0.0.0.0", port=port)
    finally:
        print("\nShutting down tunnel...")
        ngrok.kill()


if __name__ == "__main__":
    main()