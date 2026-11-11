$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    uv run energy-home @args
    if ($LASTEXITCODE -ne 0) {
        throw "Не удалось запустить Energy Home (код $LASTEXITCODE)."
    }
}
finally {
    Pop-Location
}
