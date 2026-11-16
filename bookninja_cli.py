# Command-line entry: python bookninja_cli.py book.m4b --max 5h
import sys

from bookninja.cli import main

sys.exit(main())
