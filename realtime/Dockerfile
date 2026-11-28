# 어닝사무라이 실시간 서버 (Railway). 저장소 전체를 넣는다 — 기존 수집기 함수를 그대로 부른다.
# 크로미움 설치(playwright --with-deps)가 데비안 trixie 를 못 받는다 — bookworm 으로 못박는다.
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk tzdata nodejs npm poppler-utils \
    && rm -rf /var/lib/apt/lists/*
# '왜' 요약을 회원님 Claude Max 로 돌린다 — Claude Code 를 CLAUDE_CODE_OAUTH_TOKEN 으로 부른다(brief.py).
RUN npm install -g @anthropic-ai/claude-code @openai/codex && npm cache clean --force   # 요약: Claude 계정 여럿 → 한도면 Codex(ChatGPT)
RUN pip install --no-cache-dir matplotlib==3.9.2 playwright==1.48.0 \
    && playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . /app
# 작업 줄(스레드)이 스무 개 넘게 돌아 glibc 가 줄마다 메모리 구역을 따로 잡으며 부풀었다 — 둘로 묶는다(11/28 요금 줄이기).
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data MALLOC_ARENA_MAX=2
CMD ["python", "-u", "realtime/app.py"]
