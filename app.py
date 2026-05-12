import argparse
import logging
import uuid

from dotenv import load_dotenv
load_dotenv()

import telemetry_setup  # noqa: F401  (must come before agent imports)

from agents.chatbot import chat


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-agent local chatbot REPL")
    parser.add_argument(
        "--session",
        default=None,
        help="Session ID (default: auto-generated UUID-based ID)",
    )
    parser.add_argument(
        "--actor",
        default="default",
        help="Actor (user) ID for cross-session memory (default: 'default')",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    session_id = args.session or f"local-{uuid.uuid4().hex[:8]}"
    logger = logging.getLogger("app")
    logger.info("Chatbot ready (session=%s, actor=%s). Ctrl+C to exit.", session_id, args.actor)

    while True:
        try:
            msg = input("you: ")
            if not msg.strip():
                continue
            print("bot:", chat(session_id, msg, actor_id=args.actor))
        except (KeyboardInterrupt, EOFError):
            logger.info("Exiting...")
            break


if __name__ == "__main__":
    main()
