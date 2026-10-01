// Company/product relationships are editorial research, not measured earnings correlations.
export const themes={
 semi_testers:{companies:'어드반테스트 6857',precision:'제품군 추적',why:'AI 반도체 테스트 투자와 함께 볼 지표. 회사는 2026년 4~6월 AI용 고성능 반도체 테스터 수요 확대를 설명했습니다.',limit:'웨이퍼 검사 등도 포함. 특정 회사·AI용 매출을 분리하지 못합니다.',url:'https://www.advantest.com/en/investors/financial-highlights/review/'},
 fiber:{companies:'후지쿠라 5803',precision:'용도 혼합',why:'데이터센터 광통신 투자와 연결해 볼 광섬유 제품군.',limit:'일반 통신망용도 포함. 일본 밖 공장에서 직접 수출한 물량은 제외됩니다.',url:'https://www.optic-product.fujikura.com/'},
 fiber_cable:{companies:'후지쿠라 5803',precision:'용도 혼합',why:'광섬유와 별도로 케이블 출하 흐름을 확인합니다.',limit:'데이터센터 전용·고밀도 제품을 분리하지 못합니다. 광섬유와 합산하면 공급망 중복 해석 위험이 있습니다.',url:'https://www.optic-product.fujikura.com/'},
 contact_lenses:{companies:'메니콘 7780',precision:'제품군 추적',why:'해외 콘택트렌즈 수요를 볼 수 있는 비교적 좁은 제품군.',limit:'다른 제조사·브랜드도 포함. 메니콘 해외 매출과 일치하지 않습니다.',url:'https://www.menicon.com/corporate/about/corporate-information'},
 fishing_reels:{companies:'시마노 7309',precision:'제품군 추적',why:'낚시용품 전체보다 릴 제품 수요를 좁혀 봅니다.',limit:'시마노 외 브랜드 포함. 해외 생산과 유통 재고를 함께 확인해야 합니다.',url:'https://fish.shimano.com/ja-JP'},
 camera_lenses:{companies:'탐론 7740',precision:'제품 혼합',why:'렌즈 사업의 수출 흐름을 회사 실적과 대조하는 후보 지표.',limit:'카메라 외 프로젝터용도 포함. 탐론의 자체 브랜드·OEM 매출을 분리하지 못합니다.',url:'https://www.tamron.com/global/ir/'},
 mechanical_watches:{companies:'세이코그룹 8050 · 시티즌 7762',precision:'제품군 추적',why:'시계 전체에서 기계식 손목시계만 분리해 고가 제품의 수출금액·수량·단가를 비교합니다.',limit:'브랜드·가격대별 분리는 불가. 단가 상승에는 제품 구성 변화도 작용합니다.',url:'https://www.citizenwatch-global.com/series8/index.html'},
 cards:{companies:'반다이남코 7832 / 포켓몬 등 복수 IP',precision:'브랜드 혼합',why:'원피스 등 TCG 사업과 대조할 소비재 수출 지표. 반다이남코 IR에서 관련 카드 사업을 확인할 수 있습니다.',limit:'포켓몬·원피스·트럼프가 같은 코드에 섞입니다. 포켓몬 수출액이나 닌텐도 매출로 해석할 수 없습니다.',url:'https://www.bandainamco.co.jp/files/ir/financialstatements/en_2026_Presentation.pdf'},
 pcb:{companies:'이비덴 4062 · ABF 밸류체인',precision:'ABF 분리 불가',why:'ABF 패키지 기판 관심 시 참고할 인쇄회로 전체 지표입니다.',limit:'853400000은 인쇄회로 전체입니다. ABF 기판·아지노모토 ABF 절연필름·삼성전기 매출을 직접 추적하는 수치가 아닙니다.',url:'https://www.ajinomoto.com/innovation/rd-organizations-and-facilities/bioscience_chemicals/technology'},
 toys:{companies:'스퀴시 / BLOOM 등',precision:'스퀴시 분리 불가',why:'스퀴시는 브랜드·재질·형상에 따라 분류를 확인해야 하며, 전용 수출코드가 확인되지 않았습니다.',limit:'완구 전체를 스퀴시 수출이라고 부를 수 없습니다. 별도 브랜드 매출·판매 순위로 보완할 대상입니다.',url:'https://i-bloom-squishy.com/products/'}
};

// Shares below describe company revenue exposure, never customs/export shares.
// Customs source has commodity/country dimensions but no exporter identity.
const customs={label:'무역통계 제공 항목',url:'https://www.customs.go.jp/toukei/info/tsdl_e.htm'};
const tamronSource={label:'탐론 2025 사업별 매출',url:'https://www.tamron.com/global/ir/finance/finance_04.html'};
const shimanoSource={label:'시마노 2025 주주통신 pp.1–2',url:'https://www.shimano.com/jp/img/pdf/shareholderletter/sh_2025_12.pdf'};
const daiwaSource={label:'다이와 제품·사업 근거',url:'https://www.globeride.co.jp/-/media/Project/globeride/globeride_cojp/sustainability/report/pdf/globeride2025_web_250926.pdf'};
themes.fishing_reels.companies='시마노 7309 · 글로브라이드(다이와) 7990';
themes.fishing={...themes.fishing_reels,precision:'제품 혼합',why:'릴·낚싯대 등 일본산 낚시용품의 해외 출하를 함께 봅니다.',limit:'릴 외 제품도 포함. 기업별 생산국·제품 구성과 유통 재고가 다릅니다.'};
themes.optics={...themes.camera_lenses,precision:'광학부품 혼합',why:'탐론 렌즈 사업과 비교할 광학부품 전체의 수출 흐름입니다.',limit:'렌즈·프리즘·거울 등 제품이 섞여 카메라 렌즈보다 기업 실적과의 연결이 약합니다.'};
themes.watches={companies:'세이코그룹 8050 · 시티즌 7762 · 카시오 6952',precision:'브랜드·구동방식 혼합',why:'일본산 시계의 해외 출하 흐름을 관련 시계 사업과 비교합니다.',limit:'기계식·전기식, 완제품 가격대가 섞입니다. 해외 공장 직출하·무브먼트 사업은 별도로 확인해야 합니다.',url:'https://www.seiko.co.jp/en/ir/individual/about/'};
const exposureTamron={company:'탐론',business:'사진용 렌즈',numerator:60643,denominator:85071,period:'2025년 1–12월',note:'사진용 렌즈 사업 전체. 일본 생산·수출분만의 비중은 아님.',source:tamronSource};
const exposureShimano={company:'시마노',business:'낚시용품',numerator:110832,denominator:466243,period:'2025년 1–12월',note:'릴·낚싯대 등을 포함한 낚시용품 전체. 릴 단독 비중은 미확인.',source:shimanoSource};
const notes={
 semi_testers:{up:'반도체 검사장비 주문 증가라면 어드반테스트에 긍정적.',down:'검사장비 투자 감소라면 어드반테스트 출하에 부담.',caution:'AI용 테스터·웨이퍼 검사장비가 섞임. 경쟁사 점유율 변화만으로도 수출과 회사 실적이 달라질 수 있음.',exposureNote:'테스트 사업과 관련성이 높지만 이 HS에 대응하는 매출 비중은 확인되지 않음.'},
 fiber:{up:'광통신 수요 증가라면 후지쿠라 관련 제품에 긍정적.',down:'광섬유 주문 감소라면 후지쿠라 관련 사업에 부담.',caution:'데이터센터 외 통신망용 포함. 해외 생산 증가가 일본 수출 감소를 상쇄할 수 있음.',exposureNote:'광섬유 단독 매출 비중 미확인. 정보통신 부문 전체와 같지 않음.'},
 fiber_cable:{up:'광케이블 수요 증가라면 후지쿠라 관련 사업에 긍정적.',down:'광케이블 주문 감소라면 후지쿠라 관련 사업에 부담.',caution:'데이터센터 전용이 아님. 해외 생산·케이블 규격별 구성을 함께 확인.',exposureNote:'해당 HS 케이블의 회사 매출 비중 미확인.'},
 contact_lenses:{up:'일본산 콘택트렌즈 수요 증가라면 메니콘에 긍정적.',down:'수요 둔화에 따른 감소라면 메니콘 일본 생산분에 부담.',caution:'해외 공장 증설은 일본 수출과 반대 방향으로 작용할 수 있음. 다른 제조사도 포함.',exposureNote:'일본 생산 콘택트렌즈 수출분의 연결매출 비중 미확인.'},
 fishing_reels:{up:'일본산 릴 수요 증가라면 시마노·글로브라이드에 긍정적.',down:'릴 수요 둔화라면 두 회사 낚시 사업에 부담.',caution:'릴의 제조국·가격대·유통 재고에 따라 영향 차이. 감소가 해외생산 이전 때문이면 악재로 단정 불가.',exposures:[exposureShimano],sources:[daiwaSource]},
 camera_lenses:{up:'사진용 렌즈 수요 증가라면 탐론에 긍정적.',down:'렌즈 수요 둔화라면 탐론 자체 브랜드·OEM 사업에 부담.',caution:'다른 제조사·프로젝터 렌즈 포함. OEM 고객 주문과 해외생산을 별도 확인.',exposures:[exposureTamron]},
 mechanical_watches:{up:'일본산 기계식 수요 증가라면 세이코·시티즌에 긍정적.',down:'기계식 수요 둔화라면 관련 제품 출하에 부담.',caution:'고가 제품 비중 상승만으로 금액이 늘 수 있음. 전기식 중심 제품의 신호로 사용하지 않음.',exposureNote:'각 회사의 기계식 시계 단독 매출 비중 미확인.'},
 cards:{up:'자사 TCG 출하가 늘었다면 반다이남코 카드 사업에 긍정적.',down:'자사 TCG 수요 둔화라면 카드 사업에 부담.',caution:'포켓몬·원피스·트럼프 혼합. 수출 증가를 반다이남코 또는 닌텐도의 매출 증가로 바로 연결할 수 없음.',exposureNote:'TCG 단독 매출 비중 미확인. 완구·취미 부문 전체 비중을 카드 비중으로 대체하지 않음.'},
 pcb:{up:'고성능 패키지 기판 출하 증가라면 이비덴에 긍정적일 수 있음.',down:'ABF 관련 출하 감소가 확인되면 이비덴에 부담 가능.',caution:'인쇄회로 전체로 ABF 분리 불가. 아지노모토 절연필름·삼성전기 매출 방향은 이 통계만으로 판단 불가.',exposureNote:'ABF 단독 비중 미확인. 전자부문 매출 비중을 ABF 비중으로 사용하지 않음.',sources:[{label:'이비덴 사업 구성',url:'https://www.ibiden.com/ir/library/'}]},
 toys:{up:'브랜드별 출하 증가가 확인된 제조사에 긍정적일 수 있음.',down:'브랜드별 수요 둔화가 확인된 제조사에는 부담 가능.',caution:'BLOOM·스퀴시만의 수치가 아님. 특정 상장사 수혜·피해를 지정하기 어려움.',exposureNote:'스퀴시의 기업별 매출·수출 비중 미확인.'},
 watches:{up:'일본산 완제품 수요 증가라면 세이코·시티즌·카시오에 긍정적.',down:'완제품 수요 둔화라면 해당 회사 일본 생산분에 부담.',caution:'수량 감소 속 단가 상승, 환율, 해외 생산 이전을 구분해야 함.',exposureNote:'손목시계 일본 수출분의 회사 매출 비중 미확인.',sources:[{label:'카시오 시계 사업',url:'https://world.casio.com/ir/library/annual/2025/'},{label:'시티즌 시계 제품',url:'https://www.citizenwatch-global.com/series8/index.html'}]}
};
const citizenSource={label:'시티즌 2025년 3월기 사업별 외부매출',url:'https://www.citizen.co.jp/cms/cwc/global/files/citizen_report_r2025e.pdf'};
const exposureCitizen={company:'시티즌',business:'시계 사업 전체',numerator:177121,denominator:316885,period:'2024년 4월–2025년 3월',note:'기계식 단독·일본 수출 비중이 아님. 외부고객 매출 기준.',source:citizenSource};
notes.watches.exposures=[exposureCitizen];
notes.mechanical_watches.exposures=[exposureCitizen];
notes.fishing={...notes.fishing_reels,caution:'릴 외 낚시용품을 포함. 해외 생산과 도매 재고 변화는 일본 수출만으로 확인 불가.'};
notes.optics={...notes.camera_lenses,caution:'광학부품 전체의 혼합 지표. 탐론이 일본 광학 수출을 지배한다는 의미가 아님.'};
for(const [id,t] of Object.entries(themes)){
 const n=notes[id];
 t.impact={...n,exportShare:null,reviewedAt:'2026-10-01',sources:[{label:'기업·제품 근거',url:t.url},customs,...(n.sources||[]),...(n.exposures||[]).map(e=>e.source)]};
}
