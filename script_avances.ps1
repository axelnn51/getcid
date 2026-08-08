$ErrorActionPreference = "Stop"

# Obtener la fecha actual para el nombre del archivo
$fecha = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$nombreZip = "AVANCE_$fecha.zip"

# Directorio donde se guardarán los avances
$dirAvances = "AVANCES"

# Crear la carpeta AVANCES si no existe
if (-not (Test-Path $dirAvances)) {
    New-Item -ItemType Directory -Force -Path $dirAvances | Out-Null
}

$rutaDestino = Join-Path -Path $dirAvances -ChildPath $nombreZip

Write-Host "Comprimiendo el proyecto..." -ForegroundColor Cyan

# Comprimir todo excluyendo las carpetas pesadas/innecesarias como node_modules, .git, etc.
# Note: Compress-Archive doesn't easily exclude folders, so we use a temporary folder approach or just exclude by wildcards.
# To make it simple and bulletproof, we will copy what we want to a temp folder and zip it, or use a zip module.
# Let's just exclude common big folders:
$exclude = @("AVANCES", "frontend\node_modules", ".git", "playwright_data", "logs", "*.zip")
$tempFolder = Join-Path -Path $env:TEMP -ChildPath "GETCID_AVANCE_$fecha"

New-Item -ItemType Directory -Force -Path $tempFolder | Out-Null

Write-Host "Copiando archivos..."
# Copiar todos los archivos filtrando los excluidos
Get-ChildItem -Path . -Exclude $exclude | Copy-Item -Destination $tempFolder -Recurse -Force

Write-Host "Generando archivo ZIP..."
Compress-Archive -Path "$tempFolder\*" -DestinationPath $rutaDestino -Force

# Limpiar temporal
Remove-Item -Path $tempFolder -Recurse -Force

Write-Host "¡Avance guardado con éxito en: $rutaDestino!" -ForegroundColor Green
Write-Host "Presiona cualquier tecla para salir..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
