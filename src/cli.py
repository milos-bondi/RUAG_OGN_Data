"""Command line entrypoints for the application."""

import uvicorn

from src.envs import env


def main() -> None:
    """Run the API server and startup collector with one command."""
    uvicorn.run("src.router:app", host=env.host, port=env.port)


if __name__ == "__main__":
    main()
