@echo off
REM Abre um TUNEL PUBLICO (Cloudflare) apontando pro servidor local na porta 8731.
REM
REM Como usar:
REM   1) Deixe o run.bat rodando numa janela (o servidor local).
REM   2) Rode este tunnel.bat numa OUTRA janela.
REM   3) Depois de alguns segundos aparece uma linha com uma URL do tipo
REM      https://alguma-coisa.trycloudflare.com  -- essa e a URL publica.
REM      Ela muda toda vez que voce reinicia o tunel.
REM
REM ATENCAO: essa URL ainda NAO tem senha -- qualquer pessoa com o link consegue
REM editar os dados de producao. Nao publique o link em lugar aberto, mande so
REM pra quem precisa mesmo.
REM
REM Pre-requisito (uma vez so): baixe o cloudflared.exe em
REM   https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
REM e salve com esse nome exato ("cloudflared.exe") nesta mesma pasta do projeto.

setlocal
cd /d "%~dp0"

if not exist "cloudflared.exe" (
    echo cloudflared.exe nao encontrado nesta pasta.
    echo Baixe em:
    echo   https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
    echo e salve como "cloudflared.exe" nesta mesma pasta do projeto.
    echo.
    pause
    exit /b 1
)

echo Abrindo tunel publico para http://localhost:8731 ...
echo A URL publica aparece abaixo em alguns segundos (procure por "trycloudflare.com").
echo.

cloudflared.exe tunnel --url http://localhost:8731

endlocal
