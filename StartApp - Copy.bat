@echo off
cd /d "%~dp0"

:: Start Ollama with Yellow text (color code 0A)
echo [Ollama] Starting in Green...

start /b "" cmd /c "ollama.exe serve  | color /a:06 .*"
color 0A
:: Wait for Ollama to initialize
timeout /t 3 /nobreak >nul

:: Start Streamlit with CYAN text (color code 0B) in the foreground
echo [Streamlit] Starting in Cyan...
color 0B
streamlit run main.py | color /a:0B .*