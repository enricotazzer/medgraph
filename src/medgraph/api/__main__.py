"""Serve the local app on 127.0.0.1 (``make app``): ``python -m medgraph.api [--port 8000]``."""

import argparse
import logging
import sys

import uvicorn

from medgraph.api.app import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve medgraph's pages on this machine.")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = create_app()
    # The link that unlocks the app in a browser (medgraph.api.access); it is shown here only.
    sys.stdout.write(
        f"\nmedgraph: open http://127.0.0.1:{args.port}/?token={app.state.secret}\n"
        "(the key in this link lets a browser use the app; keep it to yourself)\n\n"
    )
    sys.stdout.flush()
    # No access log: request paths name patients, and the first one carries the key. No Server
    # header.
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=args.port,
        access_log=False,
        server_header=False,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
