# PyInstaller spec for a self-contained atc that needs no Python on the target.
#
# onedir rather than onefile: onefile unpacks itself to a temp directory on every
# start, which is slow for the many short-lived workers atc spawns and is the
# layout antivirus products flag most often on Windows.
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Platform modules are imported lazily, so static analysis misses them.
hiddenimports = collect_submodules("agent_atc")
# Skills, templates, PowerShell, the Pi extension; the Windows desktop assets
# are .py files read as data, not imported.
datas = collect_data_files("agent_atc") + collect_data_files(
    "agent_atc", include_py_files=True, includes=["desktop_windows_assets/*.py"]
)

a = Analysis(
    [os.path.join(SPECPATH, "atc_main.py")],
    hiddenimports=hiddenimports,
    datas=datas,
    excludes=["tkinter", "unittest", "pydoc"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="atc", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="atc", upx=False)
