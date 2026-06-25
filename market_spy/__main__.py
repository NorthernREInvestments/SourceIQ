"""SourceIQ package entry point."""

from dotenv import load_dotenv

load_dotenv()

from market_spy.cli import main

if __name__ == "__main__":
    main()
