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

REM 3) Optional: scientific stack needed by the AI engine at runtime.
python -m pip install numpy scipy scikit-image

echo [*] Building single-file portable exe...
REM IMPORTANT: build from ImageStudio.spec, do NOT pass options that make
REM PyInstaller regenerate it. The spec carries settings this app needs:
REM full collection of numpy/scipy/skimage (PyInstaller otherwise misses
REM numpy 2.x's `numpy._core` and the AI engine fails to load), the bundled
REM icon.ico/icon.png, and upx=False so native DLLs are never UPX-packed.
pyinstaller --noconfirm ImageStudio.spec

echo.
echo [+] Done. Your portable app: dist\UltimateImageStudio.exe
echo     (External tools magick/cjpeg/cwebp/pngquant/oxipng must be on PATH at runtime.)
pause
