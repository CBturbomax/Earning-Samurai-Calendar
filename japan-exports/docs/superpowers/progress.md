# 실행 기록
설계/계획은 사용자 요청에 따라 추가 승인 대기 없이 실행.
신규 독립 작업 디렉터리에서 작업하므로 기존 저장소 변경과 격리됨.
1→2 interface JSON: 기간·품목·국가·단위·출처를 명시.
2→3 interface: 정적 파일 및 JSON, 추가 빌드 의존성 없음.

Task 1 complete: official CSV 7 years / 16 items, national = sum of countries, full 80 months.
Task 2 implemented: Python 7 tests / JS 4 tests pass.
Final review: independent reviewer found 3 important items; fixed cross-year unit comparisons (RED→GREEN), latest continuous 72-month validation (RED→GREEN), and raw cache staleness (always fresh source).
Ruling: use gzip data files to reduce transfer size, modern browsers required.
Ruling: standalone path in existing GitHub Pages repository preserves existing homepage and avoids unrelated settings changes.
