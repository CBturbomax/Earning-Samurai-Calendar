# usage: push.sh "msg" file...  — 작업 폴더는 건드리지 않는다. 올리기 전용 클론(${PUSH_REPO:-/tmp/es-pushrepo})에서 커밋·푸시.
msg="$1"; shift; SRC=$(pwd); ST=$(mktemp -d /tmp/es-stage.XXXX)
for f in "$@"; do mkdir -p $ST/$(dirname $f); cp "$SRC/$f" $ST/$f || { echo "COPY FAIL $f"; exit 1; }; done
exec 9>/tmp/es-push.lock; flock 9
R=${PUSH_REPO:-/tmp/es-pushrepo}
[ -d $R/.git ] || git clone -q --depth 1 https://github.com/CBturbomax/Earning-Samurai-Calendar $R
cd $R && git config user.email "${GIT_EMAIL:-noreply@anthropic.com}" && git config user.name "${GIT_NAME:-AI}"
for i in 1 2 3 4 5; do
  git fetch -q --depth=1 origin main && git reset -q --hard origin/main
  for f in "$@"; do mkdir -p $(dirname $f); cp $ST/$f $f; done
  git add "$@"; git commit -qm "$msg" || { echo nothing; exit 0; }
  git push -q origin HEAD:main 2>/dev/null && { echo pushed; exit 0; }; sleep 3
done; echo FAILED
