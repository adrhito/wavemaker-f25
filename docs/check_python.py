"""Check this machine can run the application. Changes nothing, touches no PLC.

    py -3 docs\\check_python.py

Or, if the py launcher is not on PATH:

    C:\\Python37\\python.exe docs\\check_python.py

Written to run on Python 2.7 as well as 3.x, so that an unexpectedly old
interpreter reports a clear version error instead of a SyntaxError.
"""

import os
import sys

MIN_VERSION = (3, 7)


def main():
    ok = True
    print("Wavemaker System Control - environment check")
    print("=" * 52)

    print("Python:      %s" % sys.version.split()[0])
    print("Executable:  %s" % sys.executable)
    print("Platform:    %s" % sys.platform)
    print("")

    if sys.version_info < MIN_VERSION:
        print("FAIL  Python %d.%d or newer is required." % MIN_VERSION)
        print("      Windows 7 supports Python up to 3.8; install 3.8 if you can,")
        print("      otherwise 3.7. Both work.")
        return 1
    print("OK    Python version is new enough.")

    if sys.version_info >= (3, 9) and sys.platform == "win32":
        print("NOTE  Python 3.9+ does not support Windows 7. If this is the lab")
        print("      PC, something is unusual -- worth double checking.")

    # tkinter ships with the standard Windows installer but can be missing.
    try:
        import tkinter  # noqa: F401
    except ImportError as exc:
        print("FAIL  tkinter is not available (%s)." % exc)
        print("      Re-run the Python installer and enable 'tcl/tk and IDLE'.")
        return 1
    print("OK    tkinter is available.")

    # Import every module of the application.
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, app_dir)
    print("App:         %s" % app_dir)
    print("")

    modules = [
        "app.paths",
        "app.tags",
        "app.params",
        "app.plc",
        "app.simulator",
        "app.drive_status",
        "app.waves",
        "app.patterns",
        "app.external",
        "modules.eip",
        "modules.tooltip",
        "modules.logging.log_utils",
        "Motor",
        "Model",
        "style",
        "operate.Operate",
        "operate.ParameterDialog",
        "operate.PatternDialog",
        "modules.widgets",
        "modules.tank_view",
        "preset_options.Preset",
        "preset_options.PresetProcessor",
        "preset_options.PresetOptions",
        "feedback.Feedback",
        "View",
        "main",
    ]
    for name in modules:
        try:
            __import__(name)
        except Exception as exc:
            print("FAIL  %-32s %s: %s" % (name, type(exc).__name__, exc))
            ok = False
        else:
            print("OK    %s" % name)

    print("")

    # pymongo is optional; say so rather than reporting a failure.
    try:
        import pymongo  # noqa: F401
    except ImportError:
        print("NOTE  pymongo is not installed. That is fine -- analytics are")
        print("      written to analytics\\<date>.txt either way.")
    else:
        print("OK    pymongo present; analytics will also go to MongoDB.")

    # The presets must be readable.
    try:
        from app import paths
        from preset_options.PresetProcessor import PresetProcessor

        processor = PresetProcessor()
        files = sorted(paths.PRESET_DIR.glob("*.csv"))
        bad = []
        for path in files:
            try:
                processor.load(str(path))
            except Exception as exc:
                bad.append("%s (%s)" % (path.name, exc))
        if bad:
            print("WARN  %d preset(s) would not load:" % len(bad))
            for entry in bad:
                print("        %s" % entry)
        else:
            print("OK    all %d preset files load." % len(files))
    except Exception as exc:
        print("WARN  could not check presets: %s" % exc)

    print("")
    print("=" * 52)
    if ok:
        print("READY - double-click 'Open Wavemaker.cmd' to start.")
        print("Nothing here contacted the PLC.")
        return 0
    print("NOT READY - see the FAIL lines above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
