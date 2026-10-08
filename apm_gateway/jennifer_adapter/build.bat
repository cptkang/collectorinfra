@echo off
rem Build the JENNIFER event adapter jar on Windows (no Maven / no internet needed).
rem Usage:  set "EXT_JAR=C:\path\to\extension-1.5.8.jar"
rem         build.bat
rem Output: dist\collectorinfra-jennifer-adapter-<version>.jar  (extension jar is NOT bundled)
rem This file is ASCII-only on purpose: cmd reads .bat files in the console code page (cp949),
rem so UTF-8 Korean text could corrupt neighbouring characters.
setlocal
cd /d "%~dp0"

where javac >nul 2>&1 || ( echo [ERROR] javac not found. Install a JDK 8-21 and add its bin folder to PATH. & exit /b 2 )
where jar   >nul 2>&1 || ( echo [ERROR] jar not found. Use a JDK, not a JRE. & exit /b 2 )
if "%EXT_JAR%"=="" ( echo [ERROR] Set EXT_JAR first, e.g.  set "EXT_JAR=C:\jennifer\extension-1.5.8.jar" & exit /b 2 )
if not exist "%EXT_JAR%" ( echo [ERROR] EXT_JAR file not found: "%EXT_JAR%" & exit /b 2 )

set "VERSION=0.2.0"
set "OUT=dist\collectorinfra-jennifer-adapter-%VERSION%.jar"
set "SRC=src\main\java\com\collectorinfra\jennifer\adapter"

echo extension jar : "%EXT_JAR%"
javac -version

rem Java 8 bytecode so it loads on any view server JVM (8 / 17 / 21). JDK 9+ uses --release 8.
set "TARGET=-source 1.8 -target 1.8"
javac --release 8 -version >nul 2>&1 && set "TARGET=--release 8"

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
mkdir build\classes
mkdir dist

rem Relative paths only - absolute paths containing spaces break javac @argfiles.
for %%f in ("%SRC%\*.java") do echo %SRC%\%%~nxf>> build\sources.txt

javac %TARGET% -encoding UTF-8 -nowarn -cp "%EXT_JAR%" -d build\classes @build\sources.txt || ( echo [ERROR] compile failed & exit /b 1 )
jar cf "%OUT%" -C build\classes . || ( echo [ERROR] jar failed & exit /b 1 )

echo --- verify (keep this output with the change record)
for /f %%n in ('jar tf "%OUT%" ^| find /c ".class"') do set "NCLASS=%%n"
echo classes       : %NCLASS% (expected 10)
if not "%NCLASS%"=="10" ( echo [ERROR] unexpected class count & exit /b 1 )
jar tf "%OUT%" | find "com/aries/" >nul && ( echo [ERROR] com/aries classes leaked into the jar & exit /b 1 )
for /f "tokens=*" %%v in ('javap -v -cp "%OUT%" com.collectorinfra.jennifer.adapter.AlarmEventAdapter ^| find "major version"') do echo bytecode      : %%v (52 = Java 8)
for /f "tokens=*" %%h in ('certutil -hashfile "%OUT%" SHA256 ^| find /v ":"') do echo sha256        : %%h
echo done          : %CD%\%OUT%
endlocal
