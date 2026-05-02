@echo off
:: Restore all RGB — runs at 7:30 AM

:: Switch iCUE to on profile (covers AIO + Commander Core + Lighting Node)
start "" "C:\Program Files\Corsair\Corsair iCUE5 Software (1)\iCUE.exe" --launchProfile "on"

:: Wait for iCUE to apply profile
timeout /t 3 /nobreak >nul

:: Restore OpenRGB devices with white (adjust color to your preference)
start "" /min "C:\Program Files\OpenRGB\OpenRGB.exe" --server --noautoconnect
timeout /t 4 /nobreak >nul
"C:\Program Files\OpenRGB\OpenRGB.exe" --client 127.0.0.1 --color FFFFFF
