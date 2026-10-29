# 어닝사무라이 실시간 서버 (Railway). 저장소 전체를 넣는다 — 기존 수집기 함수를 그대로 부른다.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk tzdata nodejs npm \
    && rm -rf /var/lib/apt/lists/*
# '왜' 요약을 회원님 Claude Max 로 돌린다 — Claude Code 를 CLAUDE_CODE_OAUTH_TOKEN 으로 부른다(brief.py).
RUN npm install -g @anthropic-ai/claude-code && npm cache clean --force
RUN pip install --no-cache-dir matplotlib==3.9.2
WORKDIR /app
COPY . /app
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
CMD ["python", "-u", "realtime/app.py"]
