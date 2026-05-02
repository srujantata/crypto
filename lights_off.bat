@echo off
:: Kill all RGB — runs at 10:00 PM

:: Switch iCUE to off profile (covers AIO + Commander Core + Lighting Node)
start "" "C:\Program Files\Corsair\Corsair iCUE5 Software (1)\iCUE.exe" --launchProfile "off"

:: Wait for iCUE to apply profile
timeout /t 3 /nobreak >nul

:: Start OpenRGB server then kill all other device lights
start "" /min "C:\Program Files\OpenRGB\OpenRGB.exe" --server --noautoconnect
timeout /t 4 /nobreak >nul
"C:\Program Files\OpenRGB\OpenRGB.exe" --client 127.0.0.1 --color 000000
