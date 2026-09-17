"""Run on Pi: python main.py --config config/pi.json; simulate: --mock."""
import argparse
import logging
import multiprocessing
import signal

from assistive.config import load_config


def main():
    parser = argparse.ArgumentParser(description="Second Sight Raspberry Pi runtime")
    parser.add_argument("--config", default="config/pi.json")
    parser.add_argument("--mock", action="store_true", help="Simulate camera, AI, ToF and audio")
    parser.add_argument("--duration", type=float, help="Stop after N seconds, useful for bench tests")
    parser.add_argument("--mock-button-after", type=float, help="Inject one OCR button in mock mode")
    parser.add_argument("--check", action="store_true", help="Validate config, models and dependencies")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(threadName)s %(levelname)s %(message)s")
    config = load_config(args.config)
    if args.check:
        from assistive.preflight import check
        return 0 if check(config, mock=args.mock) else 1
    from assistive.app import AssistiveApp
    app = AssistiveApp(config, mock=args.mock)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: app.stop_event.set())
    try:
        app.run(duration=args.duration, mock_button_after=args.mock_button_after)
    except Exception:
        logging.exception("Application stopped after failure; systemd may restart it")
        return 1
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
