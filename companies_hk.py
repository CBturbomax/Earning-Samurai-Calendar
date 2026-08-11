# -*- coding: utf-8 -*-
"""
홍콩 주목종목 사전 — 종목코드: (한글명, 영문명, 테마그룹)

코드는 HKEX 표기대로 5자리 0채움을 쓴다. 텐센트는 700 이 아니라 `00700`.
소스가 4자리로 주든 5자리로 주든 scrape_hk.py 가 5자리로 맞춰서 저장한다.

한자 사명을 한국 한자음으로 읽으면 대부분 틀린다. 騰訊은 '등신'이 아니라
텐센트고, 比亞迪는 '비아적'이 아니라 BYD다. 중국어는 훈독이 아니라 음이
다른 것이라 일본어(translit.py)식 접근이 아예 통하지 않는다.
그래서 홍콩은 기계 변환을 아예 하지 않는다 — 사전에 있으면 한글명,
없으면 소스가 주는 영문명을 그대로 쓰고, 한자 원문은 늘 병기한다.
"""

GROUP_ORDER = [
    "인터넷·플랫폼",
    "반도체·하드웨어",
    "전기차·배터리",
    "소비",
    "제약·바이오",
    "금융",
    "에너지·자원",
    "통신·유틸리티",
    "부동산·인프라",
]

NOTABLE = {
    # ── 인터넷·플랫폼 ──────────────────────────────────────────────
    "00700": ("텐센트", "Tencent Holdings", "인터넷·플랫폼"),
    "09988": ("알리바바", "Alibaba Group", "인터넷·플랫폼"),
    "03690": ("메이퇀", "Meituan", "인터넷·플랫폼"),
    "09618": ("징둥닷컴", "JD.com", "인터넷·플랫폼"),
    "09999": ("넷이즈", "NetEase", "인터넷·플랫폼"),
    "09888": ("바이두", "Baidu", "인터넷·플랫폼"),
    "01024": ("콰이쇼우", "Kuaishou Technology", "인터넷·플랫폼"),
    "09626": ("빌리빌리", "Bilibili", "인터넷·플랫폼"),
    "09961": ("트립닷컴", "Trip.com Group", "인터넷·플랫폼"),
    "03888": ("킹소프트", "Kingsoft", "인터넷·플랫폼"),
    "00780": ("통청여행", "Tongcheng Travel", "인터넷·플랫폼"),
    "06618": ("징둥헬스", "JD Health", "인터넷·플랫폼"),
    "02423": ("KE홀딩스(베이커)", "KE Holdings", "인터넷·플랫폼"),

    # ── 반도체·하드웨어 ────────────────────────────────────────────
    "00981": ("SMIC", "Semiconductor Manufacturing International", "반도체·하드웨어"),
    "01810": ("샤오미", "Xiaomi", "반도체·하드웨어"),
    "01347": ("화훙반도체", "Hua Hong Semiconductor", "반도체·하드웨어"),
    "02382": ("서니옵티컬", "Sunny Optical Technology", "반도체·하드웨어"),
    "00285": ("BYD 일렉트로닉", "BYD Electronic", "반도체·하드웨어"),
    "02018": ("AAC 테크놀로지스", "AAC Technologies", "반도체·하드웨어"),
    "00992": ("레노버", "Lenovo Group", "반도체·하드웨어"),
    "06088": ("FIT 홍텅", "FIT Hon Teng", "반도체·하드웨어"),
    "00669": ("테크트로닉", "Techtronic Industries", "반도체·하드웨어"),
    "01385": ("상하이푸단마이크로", "Shanghai Fudan Microelectronics", "반도체·하드웨어"),

    # ── 전기차·배터리 ──────────────────────────────────────────────
    "01211": ("BYD", "BYD Company", "전기차·배터리"),
    "09866": ("니오", "NIO", "전기차·배터리"),
    "09868": ("샤오펑", "XPeng", "전기차·배터리"),
    "02015": ("리 오토", "Li Auto", "전기차·배터리"),
    "00175": ("지리자동차", "Geely Automobile", "전기차·배터리"),
    "02333": ("창청자동차", "Great Wall Motor", "전기차·배터리"),
    "02338": ("웨이차이파워", "Weichai Power", "전기차·배터리"),
    "00489": ("둥펑자동차", "Dongfeng Motor Group", "전기차·배터리"),
    "02238": ("광저우자동차", "Guangzhou Automobile Group", "전기차·배터리"),

    # ── 소비 ───────────────────────────────────────────────────────
    "02020": ("안타스포츠", "ANTA Sports Products", "소비"),
    "02331": ("리닝", "Li Ning", "소비"),
    "00291": ("화룬맥주", "China Resources Beer", "소비"),
    "00322": ("캉스푸", "Tingyi (Cayman Islands)", "소비"),
    "00151": ("왕왕", "Want Want China", "소비"),
    "01044": ("헝안국제", "Hengan International", "소비"),
    "06862": ("하이디라오", "Haidilao International", "소비"),
    "09633": ("농부산천", "Nongfu Spring", "소비"),
    "02313": ("선저우인터내셔널", "Shenzhou International", "소비"),
    "01929": ("저우다푸", "Chow Tai Fook Jewellery", "소비"),
    "06098": ("컨트리가든서비스", "Country Garden Services", "소비"),
    "01876": ("버드와이저 APAC", "Budweiser Brewing Company APAC", "소비"),

    # ── 제약·바이오 ────────────────────────────────────────────────
    "01801": ("이노벤트 바이오", "Innovent Biologics", "제약·바이오"),
    "06160": ("베이진", "BeiGene", "제약·바이오"),
    "02269": ("우시 바이오로직스", "WuXi Biologics", "제약·바이오"),
    "02359": ("우시 앱텍", "WuXi AppTec", "제약·바이오"),
    "01093": ("CSPC 제약", "CSPC Pharmaceutical Group", "제약·바이오"),
    "01177": ("중국생물제약", "Sino Biopharmaceutical", "제약·바이오"),
    "02196": ("상하이 포순제약", "Shanghai Fosun Pharmaceutical", "제약·바이오"),
    "01099": ("시노팜", "Sinopharm Group", "제약·바이오"),

    # ── 금융 ───────────────────────────────────────────────────────
    "00005": ("HSBC", "HSBC Holdings", "금융"),
    "01299": ("AIA", "AIA Group", "금융"),
    "02318": ("중국평안보험", "Ping An Insurance", "금융"),
    "01398": ("공상은행", "Industrial and Commercial Bank of China", "금융"),
    "00939": ("건설은행", "China Construction Bank", "금융"),
    "03988": ("중국은행", "Bank of China", "금융"),
    "01288": ("농업은행", "Agricultural Bank of China", "금융"),
    "03968": ("초상은행", "China Merchants Bank", "금융"),
    "00388": ("홍콩거래소", "Hong Kong Exchanges and Clearing", "금융"),
    "02628": ("중국생명보험", "China Life Insurance", "금융"),
    "06030": ("중신증권", "CITIC Securities", "금융"),
    "03908": ("중금공사(CICC)", "China International Capital Corporation", "금융"),
    "00011": ("항셍은행", "Hang Seng Bank", "금융"),
    "02388": ("BOC 홍콩", "BOC Hong Kong", "금융"),

    # ── 에너지·자원 ────────────────────────────────────────────────
    "00857": ("페트로차이나", "PetroChina", "에너지·자원"),
    "00386": ("시노펙", "China Petroleum & Chemical (Sinopec)", "에너지·자원"),
    "00883": ("CNOOC", "CNOOC", "에너지·자원"),
    "01088": ("중국선화에너지", "China Shenhua Energy", "에너지·자원"),
    "02899": ("자금광업", "Zijin Mining Group", "에너지·자원"),
    "01378": ("중국훙차오", "China Hongqiao Group", "에너지·자원"),
    "00347": ("안강강철", "Angang Steel", "에너지·자원"),
    "03323": ("중국건재", "China National Building Material", "에너지·자원"),

    # ── 통신·유틸리티 ──────────────────────────────────────────────
    "00941": ("차이나모바일", "China Mobile", "통신·유틸리티"),
    "00762": ("차이나유니콤", "China Unicom", "통신·유틸리티"),
    "00728": ("차이나텔레콤", "China Telecom", "통신·유틸리티"),
    "00002": ("CLP 홀딩스", "CLP Holdings", "통신·유틸리티"),
    "00003": ("홍콩중화가스", "Hong Kong and China Gas", "통신·유틸리티"),
    "00006": ("홍콩전등", "Power Assets Holdings", "통신·유틸리티"),
    "00836": ("화룬전력", "China Resources Power", "통신·유틸리티"),

    # ── 부동산·인프라 ──────────────────────────────────────────────
    "00001": ("CK 허치슨", "CK Hutchison Holdings", "부동산·인프라"),
    "00016": ("선훙카이부동산", "Sun Hung Kai Properties", "부동산·인프라"),
    "00688": ("중국해외발전", "China Overseas Land & Investment", "부동산·인프라"),
    "01113": ("CK 애셋", "CK Asset Holdings", "부동산·인프라"),
    "00012": ("헨더슨 랜드", "Henderson Land Development", "부동산·인프라"),
    "00101": ("항룽부동산", "Hang Lung Properties", "부동산·인프라"),
    "00823": ("링크 리츠", "Link REIT", "부동산·인프라"),
    "00066": ("MTR", "MTR Corporation", "부동산·인프라"),
    "00027": ("갤럭시 엔터테인먼트", "Galaxy Entertainment Group", "부동산·인프라"),
    "01928": ("샌즈차이나", "Sands China", "부동산·인프라"),
    "00019": ("스와이어 퍼시픽", "Swire Pacific", "부동산·인프라"),
}
