# 영상공방 — 로컬 TTS 모델(ONNX) 다운로드
# HuggingFace Supertone/supertonic-3 에서 assets/onnx/ 로 직접 받는다 (git-lfs 불필요).
# GitHub 100MB 제한 때문에 모델은 저장소에 포함하지 않고 설치 시 내려받는다.
[CmdletBinding()]
param([switch]$Force)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$onnx = Join-Path $root "assets\onnx"
$base = "https://huggingface.co/Supertone/supertonic-3/resolve/main/onnx"
$files = @(
  "duration_predictor.onnx",
  "text_encoder.onnx",
  "vector_estimator.onnx",
  "vocoder.onnx",
  "tts.json",
  "unicode_indexer.json"
)

Write-Host "TTS 모델 다운로드 (HuggingFace Supertone/supertonic-3, ~380MB)" -ForegroundColor Cyan
Write-Host ("  target: " + $onnx)
New-Item -ItemType Directory -Force $onnx | Out-Null

foreach ($f in $files) {
  $dest = Join-Path $onnx $f
  if ((Test-Path $dest) -and (-not $Force)) {
    Write-Host ("  skip (있음): " + $f)
    continue
  }
  Write-Host ("  내려받는 중: " + $f + " ...")
  try {
    Invoke-WebRequest -Uri ($base + "/" + $f) -OutFile $dest -UseBasicParsing
  } catch {
    Write-Host ("  [오류] " + $f + " 다운로드 실패: " + $_.Exception.Message) -ForegroundColor Red
    Write-Host "  인터넷 연결을 확인하고 setup.bat 을 다시 실행하세요." -ForegroundColor Yellow
    exit 1
  }
}
Write-Host "TTS 모델 준비 완료." -ForegroundColor Green
