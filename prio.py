"""채우기 우선순위(회원님 11/14: "소비재(B2C·인터넷…) 먼저, 그다음 주요 산업재, 그다음 나머지 — 시총보다도").
tier(key, 업종) -> 0 소비재·인터넷 / 1 주요 산업재·기술 / 2 나머지. 대기줄은 (tier, 시총 큰 순)으로 선다. AGENT_TIER=0|01|012 로 단계를 고른다."""
import re

US0 = re.compile(r"apparel|auto & home supply|auto manufacturing|automotive aftermarket|beverages|books|broadcasting|cable &|catalog/specialty|"
                 r"clothing/shoe|computer software|computer manufacturing|consumer electronics|consumer specialties|department/specialty|"
                 r"durable goods|edp services|food chains|food distributors|garments|home furnishings|hotels/resorts|motor vehicles|"
                 r"movies/entertainment|newspapers|ophthalmic|other consumer services|other specialty stores|package goods|packaged foods|"
                 r"publishing|^retail|recreational games|restaurants|amusement & recreation|shoe manufacturing|specialty foods|textiles|"
                 r"tobacco|advertising|business services|meat/poultry", re.I)
US1 = re.compile(r"aerospace|air freight|auto parts|construction/ag equipment|electrical products|electronic components|fluid controls|"
                 r"industrial machinery|industrial specialties|integrated freight|metal fabrications|military/government|ordnance|railroads|"
                 r"semiconductors|telecommunications equipment|computer communications|computer peripheral|radio and television|"
                 r"transportation services|trucking|power generation|pollution control|precision instruments|electronics distribution|"
                 r"medical/dental instruments|medical electronics|major chemicals|specialty chemicals|tools/hardware", re.I)
JP0 = {"サービス", "サービス業", "小売業", "食品", "食料品", "その他製造", "その他製品", "繊維", "繊維製品", "情報・通信業", "通信", "自動車", "輸送用機器"}
JP1 = {"電気機器", "機械", "精密機器", "化学", "商社", "卸売業", "ゴム", "ゴム製品", "非鉄金属製品", "非鉄金属", "金属製品", "鉄道・バス", "陸運", "陸運業", "空運", "空運業"}


def tier(key, ind):
    ind = ind or ""
    if key.startswith("jp:"):
        return 0 if ind in JP0 else 1 if ind in JP1 else 2
    return 0 if US0.search(ind) else 1 if US1.search(ind) else 2
