@echo off
echo Installing Python libraries (requests, beautifulsoup4, playwright, Pillow)...
echo.
python -m pip install --upgrade pip
python -m pip install requests beautifulsoup4 playwright Pillow
echo.
echo Downloading Chromium browser (used to take page screenshots, ~100MB)...
python -m playwright install chromium
echo.
echo Done. If you see no errors above, setup is complete.
echo You can now double-click run.bat to use the tool.
echo.
pause
