Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Media.Ocr.OcrEngine, Windows.Foundation.Diagnostics, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation.Diagnostics, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null

function Await-WinRT($asyncOp) {
    return [System.WindowsRuntimeSystemExtensions]::AsTask($asyncOp).GetAwaiter().GetResult()
}

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) {
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('en-US'))
}

$framesDir = 'd:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_ux\frames'
$sampleSecs = @(0, 5, 8, 15, 20, 25, 30, 35, 44, 48, 50, 55, 60, 64)

foreach ($s in $sampleSecs) {
    $fname = ('frame_{0:06.2f}s.jpg' -f $s)
    $fpath = Join-Path $framesDir $fname
    if (-not (Test-Path $fpath)) { 
        # try without decimals
        $fname = ('frame_{0:D3}.00s.jpg' -f $s)
        $fpath = Join-Path $framesDir $fname
        if (-not (Test-Path $fpath)) { continue }
    }
    
    $file = Await-WinRT ([Windows.Storage.StorageFile]::GetFileFromPathAsync($fpath))
    $stream = Await-WinRT ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read))
    $decoder = Await-WinRT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream))
    $bmp = Await-WinRT ($decoder.GetSoftwareBitmapAsync())
    $ocrResult = Await-WinRT ($engine.RecognizeAsync($bmp))
    
    $text = $ocrResult.Text
    $lines = ($text -split "`r?`n" | Where-Object { $_.Trim().Length -gt 0 }) -join ' | '
    Write-Host "=== Frame $s s ==="
    Write-Host $lines
    Write-Host ""
}
