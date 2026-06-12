@echo off
REM ============================================================
REM  Ultimate Image Studio - Portable .exe builder (Windows)
REM  Produces a single-file, no-console executable in .\dist
REM ============================================================

REM 1) Create / reuse a virtual environment.
REM    NOTE: for the optional AI Background Removal feature, build with
REM    Python 3.12 (onnxruntime wheels may not exist for the newest Python).
if not exist .venv (
    echo [*] Creating virtual environment...
    python -m venv .venv
)
call .venv\Scripts\activate.bat

echo [*] Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller

REM 2) Optional: AI Background Removal deps (heavy). Comment out to skip.
python -m pip install rembg onnxruntime pooch pymatting

echo [*] Building single-file portable exe...
pyinstaller --noconfirm --noconsole --onefile ^
  --name "UltimateImageStudio" ^
  --icon "icon.ico" ^
  --add-data "icon.ico;." ^
  --collect-all customtkinter ^
  --collect-all rembg ^
  --collect-all onnxruntime ^
  --collect-all pooch ^
  --collect-all pymatting ^
  ImageStudio.py

echo.
echo [+] Done. Your portable app: dist\UltimateImageStudio.exe
echo     (External tools magick/cjpeg/cwebp/pngquant/oxipng must be on PATH at runtime.)
pause
