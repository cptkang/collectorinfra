@echo off
rem 제니퍼 뷰서버(Windows)에서 빌드한다.  사용: set EXT_JAR=C:\jennifer\server.view\...\extension-x.y.z.jar  그다음  build.bat
setlocal
cd /d "%~dp0"
if "%EXT_JAR%"=="" (
  echo EXT_JAR를 지정하라. 찾기: dir /s /b C:\extension*.jar
  exit /b 2
)
if not exist "%EXT_JAR%" ( echo EXT_JAR 파일 없음: %EXT_JAR% & exit /b 2 )
set VERSION=0.2.0
set OUT=dist\collectorinfra-jennifer-adapter-%VERSION%.jar
set TARGET=-source 1.8 -target 1.8
javac --release 8 -version >nul 2>&1 && set TARGET=--release 8
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
mkdir build\classes dist
dir /s /b src\main\java\*.java > build\sources.txt
javac %TARGET% -encoding UTF-8 -nowarn -cp "%EXT_JAR%" -d build\classes @build\sources.txt || exit /b 1
jar cf %OUT% -C build\classes . || exit /b 1
echo --- 산출물 검증
jar tf %OUT% | find /c ".class"
javap -v -cp %OUT% com.collectorinfra.jennifer.adapter.AlarmEventAdapter | find "major version"
certutil -hashfile %OUT% SHA256
echo 완료: %CD%\%OUT%
