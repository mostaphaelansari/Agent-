from dotenv import load_dotenv
load_dotenv()

import telemetry_setup  # noqa: F401  (must come before agent imports)

from agents.chatbot_agent import chat

session = "local-session-1"
print("Chatbot ready. Ctrl+C to exit.")
while True:
    try:
        msg = input("you: ")
        if not msg.strip():
            continue
        print("bot:", chat(session, msg))
    except (KeyboardInterrupt, EOFError):
        print("\nExiting...")
        break
