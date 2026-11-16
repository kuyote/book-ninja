"""python -m bookninja                  -> opens the GUI
   python -m bookninja a.m4b b.m4b      -> opens the GUI with those books added
   python -m bookninja a.m4b --max 5h   -> command line (any --option means CLI)"""
import sys

if any(a.startswith("-") for a in sys.argv[1:]):
    from .cli import main
    sys.exit(main())
else:
    from .gui import main
    main()
