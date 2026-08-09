"""Allow `python -m praxis` alongside the installed `praxis` console script."""

from praxis.cli import app

if __name__ == "__main__":
    app()
