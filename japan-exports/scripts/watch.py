"""새 달이 e-Stat 에 올라왔는가 — 목록 한 장만 본다(전체 수집은 몇 분 걸린다).

GitHub 크론은 몇 시간씩 밀려서 15분 주기라 적어 둬도 실제로는 늦었다 —
2026년 9월분(확보, 10/29 아침 공개)을 오후 3시에야 받았다. 워크플로가 이것을
1분마다 부르고, 새 달이면(종료 코드 0) 곧바로 collect·discover 를 돌린다.
"""
import html, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect import listing

ROOT = Path(__file__).resolve().parents[1]


def have():
    try:
        return json.loads((ROOT / 'data' / 'status.json').read_text()).get('latestMonth', '')
    except (ValueError, OSError):
        return ''


def main():
    _, page = listing()
    ym = re.findall(r'year=(\d{4})0(?:&amp;|&).*?month=(\d{8})', html.unescape(page))
    if not ym:
        print('목록에서 발표월을 못 읽었다', file=sys.stderr)
        return 2
    y, m = max((int(a), int(b[-2:])) for a, b in ym)
    pub = f'{y}-{m:02}'
    print(f'e-Stat 최신 {pub} · 우리 {have() or "없음"}')
    return 0 if pub > have() else 1


if __name__ == '__main__':
    sys.exit(main())
