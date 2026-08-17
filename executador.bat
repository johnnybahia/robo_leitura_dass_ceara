@echo off
setlocal EnableExtensions
chcp 65001 >nul
pushd "%~dp0"

set "LOG=log_robodass.txt"
set "DASS_HEADLESS=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

REM ===== OPCIONAL: se souber o caminho exato, descomente e preencha a linha abaixo =====
set "PYEXE_MANUAL="
REM set "PYEXE_MANUAL=C:\Users\juy\AppData\Local\Programs\Python\Python313\python.exe"
REM ====================================================================================

echo ============================================================ >> "%LOG%"
echo [%date% %time%] INICIO >> "%LOG%"

if not exist "robodass_webapp.py" (
    echo [%date% %time%] ERRO: robodass_webapp.py nao encontrado em %CD% >> "%LOG%"
    popd
    endlocal
    exit /b 9
)

set "PYEXE="

if defined PYEXE_MANUAL call :tentar "%PYEXE_MANUAL%"

if not defined PYEXE (
    for /f "delims=" %%P in ('where python 2^>nul') do call :tentar "%%P"
)
if not defined PYEXE (
    for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do call :tentar "%%P"
)
if not defined PYEXE (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :tentar "%%D\python.exe"
)
if not defined PYEXE (
    for /d %%D in ("C:\Program Files\Python3*") do call :tentar "%%D\python.exe"
)
if not defined PYEXE (
    for /d %%D in ("C:\Python3*") do call :tentar "%%D\python.exe"
)

if not defined PYEXE goto :sem_python

echo [%date% %time%] Interpretador: %PYEXE% >> "%LOG%"

REM Codigos 3 (Chrome nao subiu), 4 (download nao veio) e 11 (leitura da tabela
REM instavel) sao falhas passageiras do portal/maquina: vale repetir antes de
REM desistir do dia inteiro.
set "TENTATIVA=0"

:rodar
set /a TENTATIVA+=1
"%PYEXE%" robodass_webapp.py >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

if "%RC%"=="0" goto :fim
if %TENTATIVA% GEQ 3 goto :fim
if "%RC%"=="3" goto :aguardar
if "%RC%"=="4" goto :aguardar
if "%RC%"=="11" goto :aguardar
goto :fim

:aguardar
echo [%date% %time%] Codigo %RC% (falha passageira) - nova tentativa em 120s >> "%LOG%"
timeout /t 120 /nobreak >nul
goto :rodar

:fim
echo [%date% %time%] FIM - codigo de saida %RC% (tentativas: %TENTATIVA%) >> "%LOG%"
popd
endlocal & exit /b %RC%


:sem_python
echo [%date% %time%] ERRO: nenhum Python com selenium+requests+webdriver_manager foi encontrado. >> "%LOG%"
echo [%date% %time%] Rode no cmd: where python   e   py -0p   e preencha PYEXE_MANUAL neste .bat. >> "%LOG%"
popd
endlocal
exit /b 8


:tentar
if defined PYEXE goto :eof
if not exist %1 goto :eof
%1 -c "import selenium,requests,webdriver_manager" >nul 2>&1
if errorlevel 1 (
    echo [%date% %time%] Descartado, faltam dependencias: %~1 >> "%LOG%"
    goto :eof
)
set "PYEXE=%~1"
goto :eof
