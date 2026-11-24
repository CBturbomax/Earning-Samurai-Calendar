#!/usr/bin/env bash
# merge 충돌 풀기 — 워크플로마다 **제 파일만** 지킨다(11/04: collect·numbers 가 남의 파일
# guide_site.json·segments_ai.json 충돌로 멈춰 일정·실적 수치 저장이 몇 시간 끊겼다).
#   제 파일(인자 정규식)          -> 우리 것
#   index.html · version.txt     -> 우리 것(반영 뒤 다시 만든다)
#   그 밖의 data/ (남의 것·빌드 생성물) -> 저쪽 것(주인이 쓴 최신)
#   코드                          -> 덮지 않고 실패
# 사용: bash .github/scripts/resolve_merge.sh '^data/(earnings|caps)\.json$'
set -uo pipefail
own="${1:-^$}"
stuck=$(git diff --name-only --diff-filter=U)
[ -z "$stuck" ] && exit 0
code=$(echo "$stuck" | grep -v -e '^data/' -e '^index\.html$' -e '^version\.txt$' || true)
if [ -n "$code" ]; then
  echo "코드 충돌 — 자동으로 덮어쓰지 않는다:"; echo "$code"; exit 1
fi
while IFS= read -r f; do
  [ -z "$f" ] && continue
  if [ "$f" = index.html ] || [ "$f" = version.txt ] || echo "$f" | grep -Eq "$own"; then
    git checkout --ours -- "$f" && echo "  충돌 $f -> 우리 것"
  else
    git checkout --theirs -- "$f" && echo "  충돌 $f -> 저쪽 것(남의 파일)"
  fi
  git add -- "$f"
done <<< "$stuck"
if git diff --name-only --diff-filter=U | grep -q .; then
  echo "충돌을 못 풀었다:"; git diff --name-only --diff-filter=U; exit 1
fi
git commit --no-edit
