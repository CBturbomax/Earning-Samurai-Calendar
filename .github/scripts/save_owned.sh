#!/usr/bin/env bash
# 수집분 저장 — **merge 하지 않는다. 제 파일만 최신 main 위에 얹어 민다**(11/04).
#
# 예전 방식(커밋 → git fetch → merge)은 저장소가 커지면서(.git 6GB, 31MB index.html 이
# 몇 분마다 커밋된다) 얕은 클론의 fetch 하나가 17분이 걸렸다. 그 사이 다른 워크플로가
# main 에 대여섯 번 커밋하므로 merge 는 늘 남의 파일(빌드 생성물)에서 충돌했고, 충돌이면
# 멈추게 돼 있어 일정·실적 수치가 몇 시간 동안 한 번도 저장되지 않았다.
#
# 지금: 제 파일을 옆에 빼 두고 → main 끝 커밋만 받아(--depth=1, 몇 초) → 그 위에 제 파일을
# 덮고 → 커밋·푸시. 파일마다 주인이 하나라 충돌이 날 자리가 구조적으로 없다. 푸시에
# 지면(그 몇 초 사이 누가 밀었으면) 같은 일을 다시 한다.
#
# 사용: bash .github/scripts/save_owned.sh "커밋 메시지" 파일1 파일2 ...
set -uo pipefail
msg="$1"; shift
tmp="${RUNNER_TEMP:-/tmp}/owned.$$"
rm -rf "$tmp"; mkdir -p "$tmp"
have=()
for f in "$@"; do
  if [ -f "$f" ]; then mkdir -p "$tmp/$(dirname "$f")"; cp "$f" "$tmp/$f"; have+=("$f"); fi
done
[ ${#have[@]} -eq 0 ] && { echo "저장할 파일이 없다"; exit 0; }
# 실패하면 다음 단계가 올리는 복구본
tar czf "${RUNNER_TEMP:-/tmp}/collected-data.tar.gz" -C "$tmp" . 2>/dev/null || true
git config user.name  "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
for i in 1 2 3 4 5 6 7 8; do
  t0=$(date +%s)
  if ! git fetch -q --depth=1 origin main; then sleep $((2*i)); continue; fi
  git reset -q --hard FETCH_HEAD
  cp -a "$tmp/." .
  git add -- "${have[@]}"
  if git diff --cached --quiet; then echo "달라진 게 없다(${have[*]})"; exit 0; fi
  git commit -q -m "$msg"
  if git push -q origin HEAD:main; then
    echo "저장함 $(git rev-parse --short HEAD) · $(( $(date +%s) - t0 ))초 · $(git show --stat --format= HEAD | tail -1)"
    exit 0
  fi
  echo "푸시에 졌다 — 다시 ($i)"; sleep $((i + RANDOM % 3))
done
echo "저장을 끝내 못 했다"; exit 1
