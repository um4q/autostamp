#!/usr/bin/env python3
"""
AutoStamp entry point.

Just run:

    python3 main.py

The first time this runs, it automatically downloads and installs
everything it needs (PyMuPDF, Pillow, NumPy, SciPy) - there's no manual
"pip install -r requirements.txt" step. If this Python can't install
packages directly into itself (common on some Linux distros, which lock
down the system Python), it transparently creates a small private virtual
environment (.autostamp-venv, next to this file) instead, installs there,
and relaunches itself inside it - still with a single `python3 main.py`.

The one thing that can't be downloaded automatically is Tkinter itself
(Python's GUI toolkit) - on Linux it's a separate OS package, not
something pip can fetch. If it's missing, you'll get a one-line command
telling you exactly what to install.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".autostamp-venv"
RELAUNCHED_MARKER = "AUTOSTAMP_BOOTSTRAPPED"

# import name -> pip requirement
REQUIRED = {
    "fitz": "pymupdf>=1.24",
    "PIL": "Pillow>=10.0",
    "numpy": "numpy>=1.24",
    "scipy": "scipy>=1.10",
}


def _venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _missing_packages() -> list:
    missing = []
    for mod_name, requirement in REQUIRED.items():
        try:
            importlib.import_module(mod_name)
        except ImportError:
            missing.append(requirement)
    return missing


def _check_tkinter() -> None:
    try:
        import tkinter  # noqa: F401
        return
    except ImportError:
        pass

    if sys.platform.startswith("linux"):
        hint = ("    sudo apt install python3-tk        (Debian/Ubuntu)\n"
                "    sudo dnf install python3-tkinter   (Fedora/RHEL)\n"
                "    sudo pacman -S tk                  (Arch)")
    elif sys.platform == "darwin":
        hint = ("    brew install python-tk\n"
                "    (or reinstall Python from https://python.org, which bundles Tk)")
    else:
        hint = "    Reinstall Python from https://python.org, making sure 'tcl/tk' is included."

    print("AutoStamp needs Tkinter (Python's GUI toolkit), and this Python doesn't have it.\n"
          "This is the one piece pip can't install for you - please run:\n\n"
          f"{hint}\n\nThen run AutoStamp again.")
    sys.exit(1)


def _pip_install(python_exe: str, packages: list) -> None:
    print(f"Installing: {', '.join(packages)} ...")
    subprocess.check_call([python_exe, "-m", "pip", "install", "--quiet", "--upgrade", *packages])


def _bootstrap() -> None:
    """Make sure PyMuPDF/Pillow/NumPy/SciPy are importable before the rest
    of the program tries to import them - installing them automatically,
    into this interpreter if possible, or into a private local venv
    otherwise (relaunching this same script inside it)."""
    if os.environ.get(RELAUNCHED_MARKER) == "1":
        # Already running inside our own freshly-built venv - if packages
        # are still missing here, installing again won't fix it.
        missing = _missing_packages()
        if missing:
            print("Setup failed - still missing: " + ", ".join(missing))
            sys.exit(1)
        return

    missing = _missing_packages()
    if not missing:
        return

    print("AutoStamp is missing some Python packages - installing them automatically...")
    try:
        _pip_install(sys.executable, missing)
        if not _missing_packages():
            return
    except subprocess.CalledProcessError:
        pass  # fall through to the private-venv fallback below

    # Installing directly into this Python didn't work - most commonly
    # because the system Python refuses direct installs (PEP 668
    # "externally-managed-environment") or isn't writable. Fall back to a
    # small virtual environment we fully control.
    print(f"Couldn't install into the current Python - setting up a private "
          f"virtual environment instead ({VENV_DIR.name})...")
    try:
        python_exe = _venv_python()
        if not python_exe.exists():
            subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])
        _pip_install(str(python_exe), list(REQUIRED.values()))
    except subprocess.CalledProcessError as exc:
        print("Automatic setup failed while installing dependencies.\n"
              "Check your internet connection, then either try again or install manually with:\n"
              f"    {sys.executable} -m pip install -r requirements.txt\n"
              f"(pip error: {exc})")
        sys.exit(1)

    print("Relaunching AutoStamp...")
    env = os.environ.copy()
    env[RELAUNCHED_MARKER] = "1"
    os.execve(str(python_exe), [str(python_exe), str(Path(__file__).resolve()), *sys.argv[1:]], env)


if __name__ == "__main__":
    _bootstrap()
    _check_tkinter()
    from autostamp.gui import main
    main()
