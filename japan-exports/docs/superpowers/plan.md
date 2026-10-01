# 일본 수출 트래커 Implementation Plan
> REQUIRED SUB-SKILL: superpowers:executing-plans.
Goal: 공식 월별 5년 데이터의 자동 갱신 GitHub 대시보드.
Architecture: 표준 Python 수집/집계 → JSON → HTML/CSS/JS 정적 페이지. GitHub Actions가 원자료 갱신과 배포를 수행.
Tech Stack: Python stdlib, JavaScript, SVG, GitHub Pages.
Spec: design.md
## Global Constraints
60개월 기본, official CSV only, 금액 천엔 원자료, 표시 억엔, 결측/0 구분, 기존 스타일 유지.
## Review Focus
미발표월 0 오인, 중복 합산, 단위 혼합, 전년자료 결측, 수집 실패 덮어쓰기.
### Task 1: Collector
Files: scripts/collect.py, tests/test_collect.py, data/exports.json
Interface: parse_csv(text, year, through_month) → records; aggregate(records, prefixes) → series.
- [ ] HS 합산, 월별값/누계 구분, 단위/결측 테스트 작성 및 실패 확인.
- [ ] 공식 원자료 수집과 검증 집계 구현.
- [ ] 테스트 통과 및 60개월 완전성 확인.
### Task 2: Dashboard
Files: index.html, style.css, app.js, tests/test_metrics.mjs
Interface: data/exports.json records read-only; change(value, base) → number|null.
- [ ] 0 분모/결측과 60개월 기간 테스트.
- [ ] 차트, 표, 필터, 즐겨찾기, 국가 상세, CSV 구현.
- [ ] 브라우저/스크린샷 확인.
### Task 3: Deployment
Files: .github/workflows/japan-exports.yml, README.md
- [ ] 실패 시 보존과 최신 데이터 검증 적용.
- [ ] GitHub 배포 및 workflow 실행 결과와 공개 URL 확인.
