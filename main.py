import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def main() -> int:
    from energy_home.app import main as run_app

    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
