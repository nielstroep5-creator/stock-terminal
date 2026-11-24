# PyInstaller recipe for the installed app (run from the repository root):
#     pyinstaller packaging/stock-terminal.spec --noconfirm
# Makes dist/Stock Terminal/ with "Stock Terminal.exe" (the app window) and "stockterminal-run.exe" (runs the app's
# background jobs, see packaging/runner.py), sharing one set of files. On macOS: dist/Stock Terminal.app.
# The Windows installer (packaging/installer.iss) packs that folder; .github/workflows/build-app.yml does both.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
hidden = collect_submodules("stockterminal") + ["stockterminal.app.__main__"] + collect_submodules("uvicorn") + ["webview"]
datas = [
    (str(ROOT / "stockterminal" / "app" / "static"), "stockterminal/app/static"),
    (str(ROOT / "companies.txt"), "."),
    (str(ROOT / "CHANGELOG.md"), "."),
    (str(ROOT / "LICENSE"), "."),
] + collect_data_files("webview") + collect_data_files("yfinance")
icon = str(ROOT / "packaging" / ("icon.ico" if sys.platform == "win32" else "icon.png"))
excludes = ["tkinter", "matplotlib", "IPython", "pytest"]

a = Analysis([str(ROOT / "packaging" / "launcher.py")], pathex=[str(ROOT)], datas=datas, hiddenimports=hidden,
             excludes=excludes)
r = Analysis([str(ROOT / "packaging" / "runner.py")], pathex=[str(ROOT)], hiddenimports=hidden, excludes=excludes)

pyz_a = PYZ(a.pure)
pyz_r = PYZ(r.pure)
app = EXE(pyz_a, a.scripts, [], exclude_binaries=True, name="Stock Terminal", icon=icon, console=False)
run = EXE(pyz_r, r.scripts, [], exclude_binaries=True, name="stockterminal-run", icon=icon, console=False)
coll = COLLECT(app, a.binaries, a.datas, run, r.binaries, r.datas, name="Stock Terminal")

if sys.platform == "darwin":
    from stockterminal import __version__
    BUNDLE(coll, name="Stock Terminal.app", icon=icon, bundle_identifier="app.stockterminal",
           info_plist={"CFBundleShortVersionString": __version__.split("-")[0], "NSHighResolutionCapable": True})
