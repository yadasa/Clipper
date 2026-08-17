from __future__ import annotations

import argparse

from .firebase_bridge import FirebaseBridge


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Clipper home-desktop Firebase worker")
    parser.add_argument("--once", action="store_true", help="Process at most one queued job and exit")
    args = parser.parse_args()

    bridge = FirebaseBridge()
    print(f"Clipper worker online as {bridge.worker_id}")
    if args.once:
        bridge.requeue_expired()
        job = bridge.claim_next()
        if not job:
            print("No queued Firebase jobs")
            return
        bridge.process_job(job)
        print(f"Completed {job['id']}")
        return
    bridge.run_forever()


if __name__ == "__main__":
    main()
