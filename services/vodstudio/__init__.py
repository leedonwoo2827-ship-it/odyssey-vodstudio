"""영상공방 (VOD Studio) — NotebookLM 슬라이드/대본 → mp4maker 번들 파이프라인.

문서공방의 /studio 패턴을 미러링하되, liteLLM 대신 NotebookLM(`nlm` CLI)을 써서
슬라이드 이미지 + per-slide 내레이션 대본을 뽑아 mp4maker 호환 번들을 만든다.

모듈:
  prompts        1/2/3단계 프롬프트 템플릿 + slides-create focus 빌더
  master_script  2단계 마스터 대본 텍스트 → 구조화된 Slide 리스트 파서
  pdf_tools      PDF 병합 + 페이지별 PNG 렌더 + 텍스트 추출 (PyMuPDF)
  bundle_builder mp4maker 번들(chNN_script.json + images) 작성/검증
  orchestrator   3단계 레시피 전체 실행(잡 단위)
  jobs           비동기 잡 매니저(시작/폴링)
"""
