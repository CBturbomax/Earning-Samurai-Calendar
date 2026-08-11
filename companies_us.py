# -*- coding: utf-8 -*-
"""
미국 주목종목 사전 — 티커: (한글명, 영문명, 테마그룹)

일본 쪽(companies.py)과 목적이 조금 다르다. 일본은 원본이 일본어라 한글명이
'읽기 위해' 필요했지만, 미국은 원본이 영문이라 그대로도 읽힌다.
여기서 한글명은 훑어보기를 빠르게 하려는 것이고, 화면에는 영문 원문을 늘 같이 낸다.

그래서 사전에 없는 종목은 억지로 음차하지 않고 영문명을 그대로 쓴다.
`엔비디아`는 도움이 되지만 `퍼스트 인더스트리얼 리얼티 트러스트`는 도움이 안 된다.

테마는 일본 사전(반도체 장비·소재·부품 …)과 짝이 맞도록 잘랐다.
AI 공급망을 앞에 두고 그 뒤에 일반 대형주를 붙인다.
"""

GROUP_ORDER = [
    "AI·반도체",
    "반도체 장비·소재",
    "빅테크·플랫폼",
    "소프트웨어·클라우드",
    "하드웨어·네트워크",
    "전기차·자동차",
    "금융",
    "제약·헬스케어",
    "소비·유통",
    "산업·항공우주",
    "에너지·소재",
    "통신·미디어",
    "부동산·유틸리티",
]

NOTABLE = {
    # ── AI·반도체 ──────────────────────────────────────────────────
    "NVDA": ("엔비디아", "NVIDIA", "AI·반도체"),
    "AVGO": ("브로드컴", "Broadcom", "AI·반도체"),
    "AMD": ("AMD", "Advanced Micro Devices", "AI·반도체"),
    "TSM": ("TSMC", "Taiwan Semiconductor (ADR)", "AI·반도체"),
    "MU": ("마이크론", "Micron Technology", "AI·반도체"),
    "INTC": ("인텔", "Intel", "AI·반도체"),
    "QCOM": ("퀄컴", "Qualcomm", "AI·반도체"),
    "TXN": ("텍사스 인스트루먼트", "Texas Instruments", "AI·반도체"),
    "ADI": ("아나로그디바이스", "Analog Devices", "AI·반도체"),
    "MRVL": ("마벨", "Marvell Technology", "AI·반도체"),
    "ARM": ("암(ARM)", "Arm Holdings", "AI·반도체"),
    "NXPI": ("NXP반도체", "NXP Semiconductors", "AI·반도체"),
    "ON": ("온세미", "ON Semiconductor", "AI·반도체"),
    "MCHP": ("마이크로칩", "Microchip Technology", "AI·반도체"),
    "MPWR": ("모놀리식 파워", "Monolithic Power Systems", "AI·반도체"),
    "SWKS": ("스카이웍스", "Skyworks Solutions", "AI·반도체"),
    "QRVO": ("코보", "Qorvo", "AI·반도체"),
    "CRDO": ("크레도", "Credo Technology", "AI·반도체"),
    "ALAB": ("아스테라랩스", "Astera Labs", "AI·반도체"),
    "COHR": ("코히런트", "Coherent", "AI·반도체"),
    "LITE": ("루멘텀", "Lumentum", "AI·반도체"),

    # ── 반도체 장비·소재 ───────────────────────────────────────────
    "AMAT": ("어플라이드 머티리얼즈", "Applied Materials", "반도체 장비·소재"),
    "LRCX": ("램리서치", "Lam Research", "반도체 장비·소재"),
    "KLAC": ("KLA", "KLA Corporation", "반도체 장비·소재"),
    "ASML": ("ASML", "ASML Holding (ADR)", "반도체 장비·소재"),
    "TER": ("테라다인", "Teradyne", "반도체 장비·소재"),
    "ENTG": ("엔테그리스", "Entegris", "반도체 장비·소재"),
    "ONTO": ("온투 이노베이션", "Onto Innovation", "반도체 장비·소재"),
    "ACLS": ("액셀리스", "Axcelis Technologies", "반도체 장비·소재"),
    "AEIS": ("어드밴스드 에너지", "Advanced Energy Industries", "반도체 장비·소재"),
    "UCTT": ("울트라 클린", "Ultra Clean Holdings", "반도체 장비·소재"),
    "CAMT": ("카메라", "Camtek", "반도체 장비·소재"),

    # ── 빅테크·플랫폼 ──────────────────────────────────────────────
    "AAPL": ("애플", "Apple", "빅테크·플랫폼"),
    "MSFT": ("마이크로소프트", "Microsoft", "빅테크·플랫폼"),
    "GOOGL": ("알파벳(구글)", "Alphabet", "빅테크·플랫폼"),
    "AMZN": ("아마존", "Amazon.com", "빅테크·플랫폼"),
    "META": ("메타", "Meta Platforms", "빅테크·플랫폼"),
    "NFLX": ("넷플릭스", "Netflix", "빅테크·플랫폼"),
    "ORCL": ("오라클", "Oracle", "빅테크·플랫폼"),
    "CRM": ("세일즈포스", "Salesforce", "빅테크·플랫폼"),
    "ADBE": ("어도비", "Adobe", "빅테크·플랫폼"),
    "IBM": ("IBM", "IBM", "빅테크·플랫폼"),
    "SAP": ("SAP", "SAP SE (ADR)", "빅테크·플랫폼"),
    "UBER": ("우버", "Uber Technologies", "빅테크·플랫폼"),
    "ABNB": ("에어비앤비", "Airbnb", "빅테크·플랫폼"),
    "BKNG": ("부킹홀딩스", "Booking Holdings", "빅테크·플랫폼"),

    # ── 소프트웨어·클라우드 ────────────────────────────────────────
    "NOW": ("서비스나우", "ServiceNow", "소프트웨어·클라우드"),
    "INTU": ("인튜이트", "Intuit", "소프트웨어·클라우드"),
    "PLTR": ("팔란티어", "Palantir Technologies", "소프트웨어·클라우드"),
    "SNOW": ("스노우플레이크", "Snowflake", "소프트웨어·클라우드"),
    "DDOG": ("데이터독", "Datadog", "소프트웨어·클라우드"),
    "MDB": ("몽고DB", "MongoDB", "소프트웨어·클라우드"),
    "CRWD": ("크라우드스트라이크", "CrowdStrike", "소프트웨어·클라우드"),
    "PANW": ("팔로알토 네트웍스", "Palo Alto Networks", "소프트웨어·클라우드"),
    "ZS": ("지스케일러", "Zscaler", "소프트웨어·클라우드"),
    "NET": ("클라우드플레어", "Cloudflare", "소프트웨어·클라우드"),
    "TEAM": ("아틀라시안", "Atlassian", "소프트웨어·클라우드"),
    "WDAY": ("워크데이", "Workday", "소프트웨어·클라우드"),
    "SHOP": ("쇼피파이", "Shopify", "소프트웨어·클라우드"),
    "APP": ("앱러빈", "AppLovin", "소프트웨어·클라우드"),

    # ── 하드웨어·네트워크 ──────────────────────────────────────────
    "DELL": ("델 테크놀로지스", "Dell Technologies", "하드웨어·네트워크"),
    "SMCI": ("슈퍼마이크로", "Super Micro Computer", "하드웨어·네트워크"),
    "ANET": ("아리스타 네트웍스", "Arista Networks", "하드웨어·네트워크"),
    "CSCO": ("시스코", "Cisco Systems", "하드웨어·네트워크"),
    "HPE": ("HPE", "Hewlett Packard Enterprise", "하드웨어·네트워크"),
    "HPQ": ("HP", "HP Inc.", "하드웨어·네트워크"),
    "VRT": ("버티브", "Vertiv Holdings", "하드웨어·네트워크"),
    "WDC": ("웨스턴디지털", "Western Digital", "하드웨어·네트워크"),
    "STX": ("씨게이트", "Seagate Technology", "하드웨어·네트워크"),
    "NTAP": ("넷앱", "NetApp", "하드웨어·네트워크"),
    "PSTG": ("퓨어스토리지", "Pure Storage", "하드웨어·네트워크"),
    "JBL": ("자빌", "Jabil", "하드웨어·네트워크"),
    "FLEX": ("플렉스", "Flex", "하드웨어·네트워크"),

    # ── 전기차·자동차 ──────────────────────────────────────────────
    "TSLA": ("테슬라", "Tesla", "전기차·자동차"),
    "GM": ("제너럴모터스", "General Motors", "전기차·자동차"),
    "F": ("포드", "Ford Motor", "전기차·자동차"),
    "RIVN": ("리비안", "Rivian Automotive", "전기차·자동차"),
    "LCID": ("루시드", "Lucid Group", "전기차·자동차"),
    "APTV": ("앱티브", "Aptiv", "전기차·자동차"),
    "MBLY": ("모빌아이", "Mobileye Global", "전기차·자동차"),

    # ── 금융 ───────────────────────────────────────────────────────
    "JPM": ("JP모건 체이스", "JPMorgan Chase", "금융"),
    "BAC": ("뱅크오브아메리카", "Bank of America", "금융"),
    "WFC": ("웰스파고", "Wells Fargo", "금융"),
    "C": ("씨티그룹", "Citigroup", "금융"),
    "GS": ("골드만삭스", "Goldman Sachs", "금융"),
    "MS": ("모건스탠리", "Morgan Stanley", "금융"),
    "BLK": ("블랙록", "BlackRock", "금융"),
    "SCHW": ("찰스슈왑", "Charles Schwab", "금융"),
    "AXP": ("아메리칸 익스프레스", "American Express", "금융"),
    "V": ("비자", "Visa", "금융"),
    "MA": ("마스터카드", "Mastercard", "금융"),
    "PYPL": ("페이팔", "PayPal", "금융"),
    "COIN": ("코인베이스", "Coinbase Global", "금융"),
    "PGR": ("프로그레시브", "Progressive", "금융"),

    # ── 제약·헬스케어 ──────────────────────────────────────────────
    "LLY": ("일라이 릴리", "Eli Lilly", "제약·헬스케어"),
    "JNJ": ("존슨앤드존슨", "Johnson & Johnson", "제약·헬스케어"),
    "UNH": ("유나이티드헬스", "UnitedHealth Group", "제약·헬스케어"),
    "ABBV": ("애브비", "AbbVie", "제약·헬스케어"),
    "MRK": ("머크", "Merck & Co.", "제약·헬스케어"),
    "PFE": ("화이자", "Pfizer", "제약·헬스케어"),
    "NVO": ("노보 노디스크", "Novo Nordisk (ADR)", "제약·헬스케어"),
    "TMO": ("써모피셔", "Thermo Fisher Scientific", "제약·헬스케어"),
    "ABT": ("애보트", "Abbott Laboratories", "제약·헬스케어"),
    "AMGN": ("암젠", "Amgen", "제약·헬스케어"),
    "GILD": ("길리어드", "Gilead Sciences", "제약·헬스케어"),
    "VRTX": ("버텍스", "Vertex Pharmaceuticals", "제약·헬스케어"),
    "REGN": ("리제네론", "Regeneron Pharmaceuticals", "제약·헬스케어"),
    "BMY": ("브리스톨 마이어스", "Bristol-Myers Squibb", "제약·헬스케어"),
    "ISRG": ("인튜이티브 서지컬", "Intuitive Surgical", "제약·헬스케어"),
    "DHR": ("다나허", "Danaher", "제약·헬스케어"),

    # ── 소비·유통 ──────────────────────────────────────────────────
    "WMT": ("월마트", "Walmart", "소비·유통"),
    "COST": ("코스트코", "Costco Wholesale", "소비·유통"),
    "HD": ("홈디포", "Home Depot", "소비·유통"),
    "LOW": ("로우스", "Lowe's", "소비·유통"),
    "TGT": ("타깃", "Target", "소비·유통"),
    "NKE": ("나이키", "Nike", "소비·유통"),
    "SBUX": ("스타벅스", "Starbucks", "소비·유통"),
    "MCD": ("맥도날드", "McDonald's", "소비·유통"),
    "KO": ("코카콜라", "Coca-Cola", "소비·유통"),
    "PEP": ("펩시코", "PepsiCo", "소비·유통"),
    "PG": ("프록터앤드갬블", "Procter & Gamble", "소비·유통"),
    "PM": ("필립모리스", "Philip Morris International", "소비·유통"),
    "MO": ("알트리아", "Altria Group", "소비·유통"),
    "CL": ("콜게이트", "Colgate-Palmolive", "소비·유통"),
    "MDLZ": ("몬델리즈", "Mondelez International", "소비·유통"),
    "LULU": ("룰루레몬", "Lululemon Athletica", "소비·유통"),

    # ── 산업·항공우주 ──────────────────────────────────────────────
    "BA": ("보잉", "Boeing", "산업·항공우주"),
    "CAT": ("캐터필러", "Caterpillar", "산업·항공우주"),
    "DE": ("디어", "Deere & Company", "산업·항공우주"),
    "GE": ("GE 에어로스페이스", "GE Aerospace", "산업·항공우주"),
    "HON": ("허니웰", "Honeywell International", "산업·항공우주"),
    "RTX": ("RTX", "RTX Corporation", "산업·항공우주"),
    "LMT": ("록히드마틴", "Lockheed Martin", "산업·항공우주"),
    "NOC": ("노스럽 그러먼", "Northrop Grumman", "산업·항공우주"),
    "GD": ("제너럴 다이내믹스", "General Dynamics", "산업·항공우주"),
    "MMM": ("3M", "3M", "산업·항공우주"),
    "UPS": ("UPS", "United Parcel Service", "산업·항공우주"),
    "FDX": ("페덱스", "FedEx", "산업·항공우주"),
    "UNP": ("유니언 퍼시픽", "Union Pacific", "산업·항공우주"),
    "ETN": ("이튼", "Eaton", "산업·항공우주"),

    # ── 에너지·소재 ────────────────────────────────────────────────
    "XOM": ("엑슨모빌", "Exxon Mobil", "에너지·소재"),
    "CVX": ("셰브런", "Chevron", "에너지·소재"),
    "COP": ("코노코필립스", "ConocoPhillips", "에너지·소재"),
    "SLB": ("슬럼버제이", "SLB", "에너지·소재"),
    "OXY": ("옥시덴털", "Occidental Petroleum", "에너지·소재"),
    "EOG": ("EOG 리소시스", "EOG Resources", "에너지·소재"),
    "PSX": ("필립스 66", "Phillips 66", "에너지·소재"),
    "MPC": ("마라톤 페트롤리엄", "Marathon Petroleum", "에너지·소재"),
    "LIN": ("린데", "Linde", "에너지·소재"),
    "FCX": ("프리포트맥모란", "Freeport-McMoRan", "에너지·소재"),
    "NEM": ("뉴몬트", "Newmont", "에너지·소재"),

    # ── 통신·미디어 ────────────────────────────────────────────────
    "T": ("AT&T", "AT&T", "통신·미디어"),
    "VZ": ("버라이즌", "Verizon Communications", "통신·미디어"),
    "TMUS": ("T모바일", "T-Mobile US", "통신·미디어"),
    "DIS": ("월트 디즈니", "Walt Disney", "통신·미디어"),
    "CMCSA": ("컴캐스트", "Comcast", "통신·미디어"),
    "WBD": ("워너브러더스 디스커버리", "Warner Bros. Discovery", "통신·미디어"),
    "SPOT": ("스포티파이", "Spotify Technology", "통신·미디어"),
    "RBLX": ("로블록스", "Roblox", "통신·미디어"),
    "EA": ("일렉트로닉 아츠", "Electronic Arts", "통신·미디어"),
    "TTWO": ("테이크투", "Take-Two Interactive", "통신·미디어"),

    # ── 부동산·유틸리티 ────────────────────────────────────────────
    "AMT": ("아메리칸 타워", "American Tower", "부동산·유틸리티"),
    "PLD": ("프로로지스", "Prologis", "부동산·유틸리티"),
    "EQIX": ("에퀴닉스", "Equinix", "부동산·유틸리티"),
    "DLR": ("디지털 리얼티", "Digital Realty Trust", "부동산·유틸리티"),
    "SPG": ("사이먼 프로퍼티", "Simon Property Group", "부동산·유틸리티"),
    "O": ("리얼티 인컴", "Realty Income", "부동산·유틸리티"),
    "NEE": ("넥스트에라 에너지", "NextEra Energy", "부동산·유틸리티"),
    "DUK": ("듀크 에너지", "Duke Energy", "부동산·유틸리티"),
    "SO": ("서던 컴퍼니", "Southern Company", "부동산·유틸리티"),
    "CEG": ("콘스텔레이션 에너지", "Constellation Energy", "부동산·유틸리티"),
}
